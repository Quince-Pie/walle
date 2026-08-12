# WayRing io_uring architecture for Linux 6.18

Status: final research result for the io_uring half of the performance study.

Target: WayRing, C23/GNU C23, raw `<linux/io_uring.h>`, Linux 6.18.

Kernel authority used for this report: the checked-in Linux `v6.18.37` snapshot,
commit `0c503cf3dde2e53614f05261ece12f9d3d4c3c20`, under
`resources/linux/`. The implementation, not a newer man page and not a header
name considered in isolation, is the final authority.

## 1. Executive verdict

The state-of-the-art design for this project is one conventional, non-SQPOLL,
non-IOPOLL ring owned by the Wayland reactor thread, configured exactly as:

```c
p.flags = IORING_SETUP_SINGLE_ISSUER
        | IORING_SETUP_DEFER_TASKRUN
        | IORING_SETUP_NO_SQARRAY
        | IORING_SETUP_SUBMIT_ALL;
```

Use 256 SQ entries and the kernel-default 512 CQ entries. Keep the sparse
fixed-file table. Register the ring in the calling task's registered-ring
table, use the registered index for every hot `io_uring_enter()` and subsequent
non-blind registration call, but retain the real ring fd for teardown and the
Linux 6.18 blind `IORING_REGISTER_SEND_MSG_RING` worker notification.

The ring is not a storage throughput engine. It is a low-queue-depth,
latency-sensitive reactor joining:

- the Wayland socket;
- the dynamic sd-bus wait contract;
- inotify;
- periodic procfs sampling (with hardware RTC sysfs removed from the ring);
- WAMR timers and callbacks;
- mostly-idle Unix/TCP streams;
- PTY and pipe streams; and
- rare DNS worker completions.

Keep kernel-linked timeouts only where expiry can launch the next useful
operation without returning to userspace: periodic-file timeout->read and
known-address timeout->socket->connect. Consolidate pure WAMR, exec/DNS retry,
and sd-bus deadlines behind one persistent absolute timeout and the existing
64-slot direct-index topology.

For that workload, the important optimizations are deterministic ownership,
one sleep/wake boundary, no normal-path io-wq, no cross-CPU SQ handoff, no
userspace allocations in ordinary completion handlers, and prompt
prioritization of Wayland input. SQPOLL,
IOPOLL, registered buffers, provided buffers, multishot receive, send
zero-copy, NAPI busy-poll, CQE32, and ZCRX all optimize a different workload
and make this bar worse.

The final reactor has these phases:

```text
user state / sd-bus process
        |
        v
Wayland dispatch_pending -> prepare_read -> flush
        |
        v
stage every SQE for this turn (no enter from a handler)
        |
        v
release-publish SQ tail -> one enter(GETEVENTS, min_complete=1)
        |
        +-- normal return, zero CQEs --> immediately enter again
        |                               (Wayland remains prepared)
        v
acquire-snapshot CQ tail
        |
        v
lightweight pre-pass: detect Wayland readiness
        |
        v
Wayland read_events or cancel_read, then dispatch promptly
        |
        v
ordinary CQ handlers in original order; they only stage future SQEs
        |
        v
release-publish CQ head -> render/coalesce -> next turn
```

There is one hardware/policy-dependent switch. For the stated latency-first
goal, do **not** set `IORING_ENTER_NO_IOWAIT`. This lets the kernel mark the
reactor's pending-I/O sleep as I/O wait, which permits schedutil and
intel_pstate to boost a frequently awakened task. An efficiency profile may
add `NO_IOWAIT` to avoid that accounting/boost. The structural architecture is
the same in both profiles.

## 2. Research method and evidentiary boundary

This result was derived from:

1. the complete project reactor, request pool, WAMR buffer ownership, module
   workloads, Wayland path, tray path, fork/PTY/pipe path, and build output;
2. the exact Linux 6.18.37 UAPI and implementation paths for setup, enter,
   submission, completion, poll, timeout, read/write, networking, fixed files,
   mapping, registration, MSG_RING, inotify, and scheduler I/O-wait handling;
3. the official libwayland client wait-loop contract; and
4. the upstream systemd sd-bus wait-loop contract.

The inspected dev shell links libwayland-client 1.25.0 and systemd 260.1. The
Wayland drain-to-`EAGAIN` behavior and sd-bus deadline conclusions were checked
against those exact project generations, not inferred from a generic
event-loop example. This matters: libwayland 1.25.0 changed the receive path
enough to make one carefully scoped multishot poll correct where an older
bounded single-receive implementation would not be.

No performance conclusion in this report is taken from existing project
benchmark notes, liburing benchmarks, third-party microbenchmarks, or public
"requests per second" comparisons. Existing comments that quote an old
benchmark percentage are not evidence here.

This is intentionally a workload proof, not an attempt to maximize a synthetic
ring's operations per second. No benchmark can turn an unnecessary kernel
thread, a second CQE, a pinned buffer, or a cross-ring wake into an advantage
for a 16 ms/1 s event-driven status bar. Conversely, the one policy choice
that depends on CPU governor and power policy is exposed as a profile instead
of being hidden behind an allegedly universal number.

## 3. What the project actually asks of the ring

### 3.1 Bounded topology

The project has a compile-time maximum of 64 module requests. A request slot is
never a general-purpose queue node: it has one stable identity and one of a
small number of state machines. Completion contexts are static and tagged in
the low four pointer bits. Outbound data and socket addresses are already
split into parallel cold arrays.

The current data-structure choices are appropriate:

- a 64-element static request array beats a hash table or tree;
- fixed-slot indexing beats descriptor lookup;
- the 1 KiB per-request outbound queues belong in a structure-of-arrays split,
  not in the hot request record;
- the 128-byte sockaddr storage is cold and belongs in a parallel array;
- userspace-only deadlines belong in one 64-element deadline array plus a
  64-bit active mask, not a pointer heap or one kernel timer per slot;
- one in-flight send per stream is the correct FIFO/lifetime invariant;
- request slots should remain non-reused so a late CQE cannot alias a new
  logical request; and
- the 128-byte request pool and standalone watcher requests should start on a
  64-byte boundary so random completions touch two lines, not three.

Adding a hash map, balanced tree, intrusive dynamic list, or per-completion
allocation would increase metadata, pointer chasing, branch entropy, and
lifetime risk at `N <= 64` without improving an operation this reactor needs.

### 3.2 Workload table

| Source | Typical shape | Ring objective |
|---|---:|---|
| Wayland | long idle waits, bursty input/configure | minimum wake-to-read latency |
| sd-bus tray | mostly idle, dynamic read/write mask, call deadlines | implement the complete bus wait contract |
| inotify | rare config changes, potentially several records | one kernel read completion, no follow-up `read(2)` |
| `/proc/stat` | about 1 KiB, usually 1 Hz | timer-to-read chain, one visible CQE |
| `/proc/meminfo` | about 2 KiB, usually 0.5 Hz | same |
| RTC sysfs | tens of bytes, usually 1 Hz | remove from latency ring; hardware callback may sleep |
| pure WAMR timer | commonly around 16 ms, callback may take milliseconds | backpressured fixed-delay scheduling |
| MPD/stream socket | up to a few KiB, mostly idle; tiny writes | fast-poll receive, ordinary send |
| PTY/exec pipe | bursty buffered reads, up to a few KiB | ordinary read/write fast-poll |
| DNS result | rare cross-thread notification | direct CQ injection via blind MSG_RING |

This mix has no sustained high queue depth, block device polling, large
network payload, NIC queue ownership, or throughput producer capable of
amortizing a polling thread.

## 4. Findings in the current implementation

### P0: SQ-head ordering is incorrect on weakly ordered CPUs

`uring_sq_space()` and `uring_submit_and_wait()` load the kernel-owned SQ head
with `memory_order_relaxed`. Linux 6.18 states the opposite explicitly in
`io_uring/io_uring.c:6-23`: userspace must use an acquire load of SQ head before
overwriting reusable SQEs.

This is not an optional optimization barrier. It is a correctness contract.
The current code happens to be hidden by x86-64 ordering. It is not a correct
raw io_uring implementation on AArch64 or another weak-memory architecture.

Fix it by caching an acquired SQ head:

- initialize `sq_head` with an acquire load before staging the first SQE;
- after every `io_uring_enter()`, refresh it with an acquire load regardless
  of the syscall result; and
- compute space from the cached value until the next enter.

Because this ring is not SQPOLL, the kernel consumes submitted SQEs only during
`enter`. There is no reason to reload the shared head for every `get_sqe()`.
The correct cached design is both stronger and cheaper.

### P1: sd-bus is not fully integrated

The loop currently polls only `POLLIN`, calls `sd_bus_flush()`, and processes
the bus only after readable completion. sd-bus requires the caller to obtain
all three before each wait:

- `sd_bus_get_fd()`;
- `sd_bus_get_events()`, which can include `POLLOUT`; and
- `sd_bus_get_timeout()`, an absolute `CLOCK_MONOTONIC` deadline in current
  systemd.

Then `sd_bus_process()` must be called after waking and until it reports no
immediately processable work. Ignoring the write mask can delay queued bus
output. Ignoring the timeout can indefinitely delay asynchronous call
timeouts or other bus-internal deadlines when no unrelated bar timer wakes.

### P1: handlers can recursively enter the ring

The local `get_sqe()` wrapper and several two-slot reservation paths call
`uring_submit_and_wait(..., 0)` when they see SQ pressure. These functions are
called from CQ handlers. An enter during CQ drain:

- breaks the simple "one acquired CQ snapshot, one released head commit"
  phase model;
- permits deferred completions to be minted behind the active snapshot;
- adds hard-to-predict syscall locations;
- makes Wayland prepared-read lifetime harder to reason about; and
- is unnecessary with a 256-entry SQ and the project's bounded pool.

Handlers must only stage. If a future invariant violation exhausts the SQ,
record a deferred-work bit, finish the CQ batch, publish CQ head, then enter at
the single phase boundary. Do not enter recursively.

### P1: a normal zero-CQE enter needlessly tears down Wayland preparation

Under `DEFER_TASKRUN`, a timeout wake can consume the local-work budget while
the linked read completion becomes runnable for the following enter. A
successful `io_uring_enter()` with zero visible CQEs is therefore normal.

The current loop proceeds to `wl_display_cancel_read()`, restarts the entire
outer loop, prepares again, and enters again. Instead, when:

- `enter` returned nonnegative; and
- an acquire CQ snapshot is empty,

immediately repeat `enter` while keeping the successful Wayland
`prepare_read` active. On `-EINTR` or another error, leave the inner loop,
cancel the Wayland read, process the signal/error, and continue normally.

### P1: Wayland can sit behind a long WAMR callback in the same CQ batch

The current loop handles CQEs strictly in array order. If a timer CQE precedes
a Wayland poll CQE, `module_on_timer` may run for several milliseconds before
`wl_display_read_events()` is called, even though compositor bytes were
already ready.

Use a lightweight CQ pre-pass that only recognizes the Wayland input and
output tags. Resolve `read_events` versus `cancel_read` immediately, dispatch
the newly queued Wayland events, and then execute non-Wayland completion
handlers in their original order. This preserves stream ordering while
removing WAMR callback time from wake-to-Wayland-read latency.

The output tag is only recorded during the pre-pass. Do not call
`wl_display_flush()` or any callback while a prepared read is unresolved;
finish `read_events`/`cancel_read` first, then flush the now-writable output
before ordinary module work.

Do not advance CQ head during the pre-pass. Under `DEFER_TASKRUN` the kernel
does not post normal CQEs behind the issuer while it is in userspace, so the
snapshot remains stable during the callbacks.

### P1: the latency build currently selects the efficiency enter policy

The current code automatically adds `IORING_ENTER_NO_IOWAIT` whenever the
feature is advertised. Linux 6.18 otherwise sets `current->in_iowait` while an
enter sleeps with pending requests. The scheduler propagates that state as
`SCHED_CPUFREQ_IOWAIT`; schedutil and intel_pstate may boost a task that wakes
frequently from such a wait.

For this session's latency/throughput goal, omit `NO_IOWAIT`. Keep it as an
explicit efficiency profile, not an automatic feature use.

### P1: the RTC sysfs read can block the Wayland issuer inline

`/sys/class/rtc/rtc0/since_epoch` looks like a tiny synthetic file, but its
execution property is hardware-dependent. Linux 6.18's kernfs poll path
returns the default readable mask. io_uring therefore accepts the initial
nonblocking read attempt and calls the sysfs show path on the issuer. The
`since_epoch` callback calls `rtc_read_time()`, which takes an interruptible
mutex and invokes the selected RTC driver's `read_time`; a real driver may do
a sleeping regmap/I2C transaction.

No io_uring flag can make that callback nonblocking. `IOSQE_ASYNC` merely moves
the risk to io-wq, adding the worker/scheduling path this ring is designed to
exclude. For the built-in datetime module, replace the sysfs timer/read chain
with a shared-scheduler deadline plus a host
`clock_gettime(CLOCK_REALTIME)` import, normally served by the vDSO. If
physical RTC state is an explicit product requirement,
read it through a separately declared worker-backed subsystem and coalesce its
result; never put it on the Wayland latency ring.

### P1: fork/exec and config-file I/O still run on the issuer

`host_register_pty_stream()` calls `forkpty()` directly, exec-stream startup
calls `pipe2()` plus `fork()`, and an exec retry can reach that path from a CQ
handler. Live reload calls `ini_load()` on a disk-backed path and can then load
new modules and fonts. These calls happen outside a prepared Wayland read, but
they still stop the only thread capable of dispatching compositor input.

No io_uring flag makes `fork()` or WAMR/font construction nonblocking, and
putting ordinary config-file I/O on this ring would introduce the very io-wq
path the design excludes. Use an explicitly named blocking-service boundary:

- a spawn worker owns `pipe2`/PTY creation and the fork/exec parent path;
- a reload worker reads and parses a fresh immutable `IniFile` snapshot and
  acquires newly referenced module/font bytes into host-owned blobs;
- workers publish host-owned result records through the same coalesced blind
  MSG_RING wake mechanism as DNS;
- the issuer alone installs returned real fds into fixed slots, swaps config
  state, creates Wayland objects, and calls WAMR; and
- a dead/quarantined request causes the issuer to close an unclaimed worker fd
  rather than install it.

The returned pipe/PTY fd stays `O_NONBLOCK|O_CLOEXEC` and open in its result
record until the issuer completes `REGISTER_FILES_UPDATE`; only then is the
ordinary fd closed. Child-side code must retain the project's existing
async-signal-safe fork-to-exec discipline. A dedicated spawn lane prevents a
slow DNS query or config read from delaying process creation, while all lanes
may share one mutex-protected completed-result list and one notification bit.
For the plain pipe case, prefer `posix_spawn()` plus file actions so no full
address-space fork is required; retain worker-side `forkpty()` only where PTY
session/controlling-terminal setup needs it.

Config application, WAMR load/instantiate from the acquired bytes, font-object
construction, and Wayland mutation remain issuer-only and can still allocate
or consume bounded CPU on this rare cold path. The latency guarantee is that
blocking path lookup, disk read, config parse, and process creation no longer
sit in an ordinary CQ handler or the prepared-read loop.
Whether WAMR construction itself can safely move off-thread belongs to the
separate WAMR research; no io_uring layout decision can make it preemptible.

### P1: closing a fixed slot does not cancel the request already using it

Module quarantine and ordinary socket/PTY/exec disconnect paths stage a direct
fixed-slot close, assuming an in-flight read/receive/send will drain naturally.
A submitted fixed-file request has already taken a reference to its resource node.
Removing the table slot retires the table's reference; it does not revoke the
request's reference or necessarily close/wake the underlying socket/pipe.

An idle socket receive can therefore remain armed indefinitely, and an
in-flight outbound buffer marked `send_drop` cannot be recycled until a CQE
that may never arrive. The dead request and file resource survive until ring
teardown.

On every resource teardown, including quarantine, cancellation must precede
close. There are two cases.

For a resource that the state machine knows is already installed in its fixed
slot:

- one `ASYNC_CANCEL` matching that fixed fd with `FD | FD_FIXED | ALL`
  cancels its connect/read/receive/send/write/poll requests;
- deactivate any shared-deadline item and cancel any independently live
  linked timer head by its exact user-data token;
- issue the fixed-fd cancellation before removing the fixed slot in the same
  published batch; and
- keep request buffers alive until every target request posts its terminal
  CQE, whether it reports normal completion, `-ECANCELED`, or another error.

A direct creator/link head whose slot installation is not yet proven needs a
short two-phase retirement. First cancel its exact token and opportunistically
cancel the fixed slot, but **do not close the slot yet**. A visible
`-EALREADY` from the exact cancel means the creator is running; retry at a
later enter boundary. Once the creator either posts its visible failure/cancel
CQE or exact cancellation reports `-ENOENT`, issue the fixed-fd cancellation
again and then close the slot. The second fd cancellation catches a dependent
that became active after the first scan.

That phase is necessary even though selected `RESOLVE_CACHED` opens and socket
creation normally finish inline. Under an exceptional asynchronous fallback,
a same-batch exact-cancel/fd-cancel/close sequence can observe `-EALREADY`,
miss an as-yet-uninstalled slot, and then let the creator install into that
slot after the close has already returned `-EBADF`. Submission order alone
does not turn asynchronous cancellation into a completion barrier.

For timeout->socket->connect, the timer and creator have distinct tokens.
Exact-cancel every phase that the local state says may be live; cancellation of
the timer head silently retires both links, while a timer that already advanced
requires the creator/fixed-slot phase above.

There is one link-specific retirement rule. If an exact-cancelled
`CQE_SKIP_SUCCESS` head returns its visible `-ECANCELED`, its not-yet-issued
dependents were cancelled with inherited CQE skip and will never produce
CQEs; the head handler must clear every such staged ownership state. If the
head had already succeeded (and was silently skipped), the second fixed-fd
cancellation plus each issued dependent's terminal CQE performs retirement.
This is why the state machine tracks head-versus-dependent ownership
explicitly and why an unproven creator is retired in two phases.

Do not reconnect or reuse the request's `user_data` state until all locally
tracked receive/read/send/write ownership bits have been retired by their old
CQEs. A fixed slot may accept a new file while an old request still references
its retired resource node, so slot identity alone is not a generation barrier.

Use `CQE_SKIP_SUCCESS` on the cancel control SQE. A successful cancellation
needs no control CQE. An exact-token cancellation can visibly return
`-ENOENT`/`-EALREADY`, and fixed-fd lookup can return `-EBADF` for a
never-installed/retired slot; handle those as state races. With
`FD|FD_FIXED|ALL`, the normal result is a nonnegative match count, including
zero, so the skipped control CQE is not an acknowledgement that a particular
ownership bit retired. The target CQEs themselves remain the lifetime
authority: every target's terminal CQE performs normal dead-request
retirement. A successfully cancelled target reports `-ECANCELED`; a target
that won the race may instead report its ordinary result.

### P1, closed: MPD encoded a Unix endpoint with a TCP port

The packaged MPD module passed its configured authority and the default TCP
port `6600` to `WR_EFFECT_SOCKET_UPSERT` even when the authority began with
`/`. The host resource transaction deliberately treats the tuple as a tagged
union: a filesystem Unix-domain authority requires port zero, while a TCP
authority may carry a port. The noncanonical `("/path", 6600)` tuple was
therefore rejected during effect preparation, before any socket or SQE was
created. Five deterministic guest restarts then led to quarantine.

This was not a failure of the raw-ring implementation. It was evidence that
transactional admission was working: an invalid capability description could
not partially publish a socket/connect chain. The defect was nevertheless a
release blocker because the module and host disagreed on the semantic ABI, and
the supervisor collapsed every result-service failure into the misleading
message `invalid worker result batch`.

The closed design is:

- the shared guest SDK canonicalizes every `/` authority to port zero and
  serializes the configured/default port only for TCP authorities, so every
  present and future module receives the invariant rather than relying on an
  MPD-specific convention;
- result service reports its exact failed stage (state, wire batch, input
  consumption, effect policy, scene, transaction, or empty INIT);
- the real MPD INIT result is checked in both bytecode and AOT protocol lanes,
  specifically requiring socket id 1, a `/` authority, and port zero; and
- a live packaged AOT instance is exercised against the configured MPD Unix
  socket, crossing worker validation, parent effect admission, direct
  `AF_UNIX` socket creation, fixed-file installation, connect, send, and
  receive rather than stopping at wire-shape validation.

This incident also establishes a test rule: a guest effect test is incomplete
if it validates only framing. Every built-in resource-producing module needs
at least one semantic host-admission or live transaction path for its actual
default configuration.

### P1, closed: transient SQ pressure was charged to the guest

Effect preparation originally returned one null value for both an invalid
guest resource plan and insufficient space in the current userspace SQ staging
batch. The latter is not a broken capacity proof: a completion-heavy turn may
already contain staged teardown/rearm work, and the next enter immediately
reclaims the SQ entries. Restarting and eventually quarantining a healthy WAMR
worker for that issuer-local condition was incorrect fault attribution.

Effect admission is now tri-state: accepted, rejected, or retry. A retry leaves
the copied worker result and its sequence ownership untouched, publishes the
already-staged SQEs through the normal single enter boundary, and re-runs
semantic preparation on the next fair result-service turn. No recursive enter
was added. Resource strings and receive buffers are allocated only after the
SQ-space check, so the retry path does not add allocator churn. A unit gate
also proves that the retry status survives the parser/resource boundary rather
than collapsing into rejection.

The same audit separated exhaustion of the deliberately bounded 64-request
host pool from guest-policy rejection. If a running module owns resources,
the supervisor performs one non-failure restart so teardown creates admission
space. If it owns none, or the retry still has no capacity, the worker enters a
distinct operator-recoverable `BLOCKED` state until configuration
reconciliation; it does not spin, consume the crash budget, or get described
as hostile. This preserves the fixed-pool efficiency decision without lying
about fault ownership.

### P0, closed: the bound Wayland input version exceeded its listener

The live package bound `wl_seat` at the minimum of the compositor-advertised
version and the linked interface's version, which was 10 under Wayland 1.25.
The resulting `wl_pointer` inherited that version, but the production listener
ended at `axis_discrete`, the event-8 member introduced in pointer version 5.
A normal version-8 high-resolution wheel event therefore reached protocol
opcode 9 (`axis_value120`) with a null function pointer. libwayland correctly
terminated the client with `listener function for opcode 9 of wl_pointer is
NULL`. A version-9 `axis_relative_direction` event would have failed next for
the same reason.

This was not an io_uring failure: the multishot poll and canonical Wayland
read/dispatch boundary delivered a valid compositor message promptly. It was
an application-level protocol-version invariant violation after dispatch.
Binding the highest available version without implementing every event that
version makes reachable is a bad client design even when the ignored events
appear optional.

The closed design now:

- fills every `wl_pointer_listener` slot through
  `axis_relative_direction`, and derives the maximum safe seat version from
  the actual listener table before binding;
- caps the audited seat contract at version 10, so a future Wayland header
  cannot silently expose a new child event before this code is reviewed;
- accumulates pointer events until `wl_pointer.frame`, gives version-8
  `axis_value120` precedence over its coupled continuous axis event, carries
  fractional 120-unit wheel detents across frames, preserves version-5-to-7
  discrete counts, and uses continuous direction only as the fallback;
- bounds delivery to 32 logical steps per frame so a pathological compositor
  value cannot monopolize the latency-sensitive issuer;
- handles the version-10 keyboard `repeated` state instead of silently
  dropping compositor-owned repeat events; and
- resets fractional state on focus/device teardown so one surface cannot
  donate a partial detent to another.

The reducer is isolated from rendering and has a wired unit test for frame
coalescing, modern/legacy precedence, multi-detent input, fractional carry,
direction reversal, reset, both axes, and invalid axis values. This incident
adds a general Wayland release gate: every bound interface version must be
paired with a listener-completeness audit and at least one live event path for
the newest reachable opcode.

### P2: inotify performs an avoidable userspace read syscall

The loop arms `POLL_ADD`, then calls `read(2)` into a stack buffer on
completion. Inotify supplies both `.poll` and `.read` and is opened
`O_NONBLOCK`. Submit one `IORING_OP_READ` directly into a persistent 4 KiB
host buffer aligned for `struct inotify_event`:

- the first nonblocking attempt reads immediately if records are queued;
- otherwise io_uring fast-poll arms on the inotify wait queue;
- readiness retries and completes the read; and
- userspace parses the CQE buffer and rearms another read.

That preserves one visible CQE while removing the follow-up syscall and one
poll-specific request state.

### P2: the 128-byte request pool is offset half a cache line

The rebuilt x86-64 host reports `sizeof(g_request_pool) == 8192`, hence each of
the 64 requests is exactly 128 bytes, but the object begins at an address whose
low six bits are `0x20`. Every individual request therefore spans three
64-byte L1 lines, and adjacent requests share the two halves of the boundary
line. This is an object-layout fact from the generated host, not a benchmark
inference.

Declare the pool itself `alignas(64)` and assert `sizeof(IORequest) == 128`.
The 128-byte stride then keeps every request in exactly two lines with no size
increase. Give the standalone Wayland, sd-bus, inotify, and shared-deadline
request objects the same explicit alignment so the property does not depend
on link order. Retain 16-byte pointer tagging as the semantic minimum; 64 bytes
is the cache-layout choice. Apply the same explicit base alignment to the 1
KiB send-ring and 128-byte sockaddr arrays rather than relying on their
favorable current link addresses.

### P2: libwayland 1.25 permits a persistent multishot input poll

The current loop removes and recreates the Wayland `POLLIN` request after every
input completion. That is the safe default for an arbitrary consumer, because
Linux 6.18 implements multishot poll as edge-triggered persistent poll. The
exact linked libwayland 1.25.0 implementation supplies the missing drain proof:

- `wl_display_connect_to_fd()` creates the connection with an unlimited input
  buffer;
- the buffer starts at 4 KiB but grows when full; and
- `wl_connection_read()` loops `recvmsg(MSG_DONTWAIT)` until it observes
  `EAGAIN`, rather than returning after one bounded receive.

Therefore select `IORING_POLL_ADD_MULTI` for the Wayland input watcher only.
Each CQE must carry `IORING_CQE_F_MORE` while the request remains live. After
`wl_display_read_events()` drains the socket, the next compositor write creates
the new readiness edge required by the persistent poll. This removes one SQE,
one poll-hash removal, and one poll-hash insertion per Wayland input wake while
retaining the canonical `prepare_read` transaction.

This optimization is versioned, not folklore. If the linked Wayland library is
replaced with an implementation that can return while the fd remains readable,
or if WayRing starts using a bounded connection buffer that can stop before
`EAGAIN`, revert this watcher to one-shot. Keep Wayland output conditional and
one-shot, and keep sd-bus one-shot: neither has the same stable event mask and
unconditional drain proof.

### P2: the DNS eventfd can be replaced by Linux 6.18 blind MSG_RING

Linux 6.18 implements `IORING_REGISTER_SEND_MSG_RING` as a blind registration
operation: the caller supplies fd `-1` and a fully initialized
`IORING_OP_MSG_RING` SQE. A DNS worker can enqueue its result under the
existing mutex, then make one syscall that creates the reactor CQE directly.

For a target `DEFER_TASKRUN` ring, the kernel routes that remote notification
to issuer-local task work. The next `GETEVENTS` enter materializes the CQE.
This removes:

- the eventfd itself;
- its fixed-file slot;
- its persistent poll request; and
- the reactor-side `read(eventfd)` syscall.

The worker must retry `EINTR` and transient allocation failure without
touching the SQ/CQ mappings. It must stop on teardown errors, and teardown must
wait for or otherwise quiesce detached workers before closing/reusing the
ring fd.

This intentionally trades the permanently armed eventfd/poll machinery for
one kernel request allocation per rare coalesced DNS notification batch. That
is favorable for this workload: DNS completion is cold, while the extra
reactor-side read and poll rearm would otherwise execute for every
notification.

The message SQE is:

```c
struct io_uring_sqe msg = {
    .opcode = IORING_OP_MSG_RING,
    .fd = ring_real_fd,
    .off = wake_user_data,
    .addr = IORING_MSG_DATA,
    .len = 0, /* the mutex-protected result list is authoritative */
};

long rc = raw_io_uring_register(
    -1, IORING_REGISTER_SEND_MSG_RING, &msg, 1);
```

`sqe.flags` and unused union fields must remain zero. The real ring fd must
stay open, which is another reason not to use
`IORING_SETUP_REGISTERED_FD_ONLY`.

Use the same coalesced CQ injection for spawn and config-read workers; the CQE
is only a doorbell, and the tagged mutex-protected result list remains the
payload and ownership authority.

### P2: socket receive ignores useful CQE state

`IORING_OP_RECV` asks the socket layer for queued-byte state and sets
`IORING_CQE_F_SOCK_NONEMPTY` when more bytes remain. The current handler
ignores `cqe->flags`.

Use the following adaptive policy:

- first receive after connect: ordinary receive;
- after a receive with `SOCK_NONEMPTY`: ordinary receive immediately;
- after a receive without `SOCK_NONEMPTY`: set
  `IORING_RECVSEND_POLL_FIRST` on the next receive.

This avoids a predictably failing transfer attempt for a mostly-idle stream
without penalizing an already-buffered burst. Never use `POLL_FIRST` on the
project's sends, pipe reads, or PTY reads.

### P2: direct creators can be linked to their first consumer

The `IORING_FEAT_LINKED_FILE` target allows an operation later in a link to
resolve a fixed slot installed by an earlier direct-creation operation at
issue time.

Use two-SQE startup/reconnect chains:

1. `OPENAT2_DIRECT | IO_LINK | CQE_SKIP_SUCCESS` -> first `READ_FIXED_FILE`;
2. `SOCKET_DIRECT | IO_LINK | CQE_SKIP_SUCCESS` -> `CONNECT_FIXED_FILE`.

For the admitted procfs samplers, linking the first read also forces any one-time
seq-file allocation and formatting setup to occur during initialization,
before the steady-state wait loop. This is a secondary benefit; the primary
win is removing a completion/re-entry boundary.

On creator success, the creator CQE is suppressed and the next operation sees
the installed slot. On creator failure, `req_set_fail()` revokes the creator's
skip, so the creator failure CQE remains visible, and transfers skip to the
failed links. The automatically cancelled dependent is therefore silent. The
creator's distinct failure tag is the chain's sole fallback/retry trigger.

If the creator succeeds but the dependent itself fails, the dependent failure
is visible normally. State machines must distinguish those two single-CQE
failure paths; they must never wait for an `-ECANCELED` CQE that link-skip
semantics intentionally suppresses.

Do not automatically add `RECV` after `CONNECT` until the state machine has an
explicit stale-CQE/generation design: connect failure would otherwise create a
cancelled receive completion that can race logical reconnect state. The
two-SQE socket/connect chain captures the safe win.

### P3: the hot enter still calls libc's variadic syscall wrapper

The current x86-64 object contains a PLT call to `syscall` at every enter.
Because the internal API already returns negative errno values, a tiny
architecture backend can issue the six-argument system call directly and
avoid the extra function boundary and libc errno conversion.

For x86-64, the audited wrapper must place argument four in `r10`, arguments
five and six in `r8`/`r9`, list `rcx`, `r11`, and `memory` as clobbers, and
return the kernel's raw negative value. Provide an AArch64 `svc 0` backend or
fall back to libc on unreviewed architectures.

This is a final-mile instruction-path cleanup, not a reason to make the ring
architecture-specific. It ranks after every P0-P2 item.

## 5. Final hot/cold ring layout

The current `Uring` is 128 bytes, but `enter_fd` begins in the second cache
line. Submission, enter, and CQ drain are also interleaved in the first line.
Use two phase-specific lines and move mapping/teardown state entirely cold.

The exact target layout on a 64-bit ABI is:

```c
typedef struct UringHot {
    /* cache line 0: SQ staging, publication, enter */
    alignas(64)
    struct io_uring_sqe *sqes;          /*  0 */
    _Atomic uint32_t    *sq_ktail;      /*  8 */
    _Atomic uint32_t    *sq_khead;      /* 16 */
    uint32_t             sq_tail;       /* 24: userspace-owned staged tail */
    uint32_t             sq_head;       /* 28: last acquire snapshot */
    uint32_t             sq_mask;       /* 32 */
    uint32_t             sq_entries;    /* 36 */
    int32_t              enter_fd;      /* 40: registered-ring index */
    uint32_t             enter_flags;   /* 44 */
    _Atomic uint32_t    *sq_kflags;     /* 48: shared SQ status flags */
    uint64_t             sq_reserved;   /* 56 */

    /* cache line 1: CQ snapshot and retirement */
    struct io_uring_cqe *cqes;          /* 64 */
    _Atomic uint32_t    *cq_khead;      /* 72 */
    _Atomic uint32_t    *cq_ktail;      /* 80 */
    uint32_t             cq_head;       /* 88: userspace-owned cached head */
    uint32_t             cq_tail;       /* 92: current acquire snapshot */
    uint32_t             cq_mask;       /* 96 */
    uint32_t             cq_entries;    /* 100 */
    uint64_t             cq_reserved[3];/* 104 */
} UringHot;

typedef struct UringCold {
    int               ring_fd;
    uint32_t          features;
    _Atomic uint32_t *sq_kdropped;
    _Atomic uint32_t *cq_kdropped; /* mapped from cq_off.overflow */
    void             *ring_mem;
    size_t            ring_sz;
    void             *sqe_mem;
    size_t            sqe_sz;
} UringCold;

typedef struct Uring {
    UringHot  hot;
    UringCold cold;
} Uring;

static_assert(ATOMIC_INT_LOCK_FREE == 2);
static_assert(sizeof(uint32_t) == 4);
static_assert(sizeof(_Atomic uint32_t) == sizeof(uint32_t));
static_assert(alignof(_Atomic uint32_t) == alignof(uint32_t));
static_assert(__atomic_always_lock_free(4, nullptr));
static_assert(alignof(UringHot) == 64);
static_assert(sizeof(UringHot) == 128);
static_assert(offsetof(UringHot, enter_fd) == 40);
static_assert(offsetof(UringHot, cqes) == 64);
static_assert(sizeof(struct io_uring_sqe) == 64);
static_assert(sizeof(struct io_uring_cqe) == 16);
```

Do not pack this structure. Natural pointer alignment and explicit line
boundaries are part of the layout.

The 64-byte boundary matches the inspected x86-64 deployment. If WayRing is
shipped on an architecture with a larger L1 line, make the line size a target
build constant and regenerate the offset assertions; the acquire/release
contract is unchanged, but pretending two 64-byte regions are distinct cache
lines would make the layout claim false.

The division is semantic:

- `get_sqe` and `enter` touch line 0;
- CQ snapshot/iteration/advance touch line 1;
- setup, registration, mapping, and teardown touch cold state; and
- no hot helper needs a mapping size or real fd.

`sq_kflags` occupies otherwise-unused line-0 space. At the outer batch
boundary, `IORING_SQ_CQ_OVERFLOW` reports a recoverable, currently active
overflow list. The UAPI word named `cq.overflow` is different: Linux 6.18
increments it only when allocating an overflow entry fails and a CQE is
actually dropped (`io_uring.c:728-747`). Compare that cold word and
`sq.dropped` once per batch and treat either change as fatal; do not poll them
inside `get_sqe` or CQ iteration.

The ring is TU-private and the helpers remain `static inline`. That lets the
compiler fold the global address and masks without exposing the layout across
an ABI.

### 5.1 SQ hot helpers

```c
static inline uint32_t uring_sq_space(const UringHot *r) {
    return r->sq_entries - (r->sq_tail - r->sq_head);
}

static inline struct io_uring_sqe *uring_get_sqe(UringHot *r) {
    if (uring_sq_space(r) == 0)
        return nullptr;

    struct io_uring_sqe *sqe = &r->sqes[r->sq_tail++ & r->sq_mask];
    *sqe = (struct io_uring_sqe){};
    return sqe;
}

static inline long uring_enter(UringHot *r, uint32_t min_complete) {
    const uint32_t tail = r->sq_tail;

    atomic_store_explicit(r->sq_ktail, tail, memory_order_release);
    const uint32_t to_submit = tail - r->sq_head;

    const long rc = raw_io_uring_enter(
        r->enter_fd, to_submit, min_complete, r->enter_flags, nullptr, 0);

    /*
     * Refresh even after an error: this is the acquire that permits all
     * subsequent SQE-slot overwrites.
     */
    r->sq_head =
        atomic_load_explicit(r->sq_khead, memory_order_acquire);
    return rc;
}
```

The full 64-byte zero initialization is deliberate. The SQE ABI overlays
`addr2`, `file_index`, `optlen`, `addr_len`, `addr3`, metadata fields, and
several opcode-specific flag words. A stale nonzero value can redirect a send,
request direct fd installation, enable metadata parsing, or cause `EINVAL`.

The current generic build emits four 16-byte stores for the zeroed aggregate.
Do not replace it with partial field writes. This reactor submits too few SQEs
for partial initialization to matter, while the correctness consequence of a
stale union byte is large.

### 5.2 CQ hot helpers

```c
static inline uint32_t uring_cq_begin(UringHot *r) {
    r->cq_tail =
        atomic_load_explicit(r->cq_ktail, memory_order_acquire);
    return r->cq_tail - r->cq_head;
}

static inline const struct io_uring_cqe *
uring_cqe_at(const UringHot *r, uint32_t ordinal) {
    return &r->cqes[(r->cq_head + ordinal) & r->cq_mask];
}

static inline void uring_cq_commit(UringHot *r, uint32_t count) {
    if (count == 0)
        return;
    r->cq_head += count;
    atomic_store_explicit(r->cq_khead, r->cq_head, memory_order_release);
}
```

CQ head is userspace-owned. Load it once at initialization and cache it; do
not reload it from shared memory in `cq_ready` and again in `cq_advance`.
Acquire CQ tail once per batch, inspect every CQE below that snapshot, and
release CQ head exactly once.

### 5.3 The four mandatory ordering edges

| Edge | Userspace operation | Why |
|---|---|---|
| SQE writes -> SQ tail | release store to `sq.tail` | kernel acquire-loads tail before consuming SQEs |
| kernel SQE reads -> slot reuse | acquire load of `sq.head` | pairs with kernel release-store after consumption |
| kernel CQE writes -> CQE reads | acquire load of `cq.tail` | pairs with kernel release publication |
| CQE reads -> CQ slot reuse | release store to `cq.head` | prevents kernel overwrite before userspace finishes |

Relaxed operations are still appropriate for:

- reading `sq.flags` as a diagnostic on this non-SQPOLL ring;
- userspace-only cached counters between synchronization points; and
- initialization fields that are immutable after setup.

The fact that `DEFER_TASKRUN` keeps normal completion publication on the issuer
does not waive the shared-ring contract. Teardown, remote task-work enqueueing,
and portability still require the documented edges.

### 5.4 Completion-dispatch code layout

Keep the ring primitives in the reactor translation unit as tiny
`static inline` functions, but keep the substantial file/socket/PTY/WAMR state
handlers out of line. The hot CQ loop should contain only:

1. the Wayland tag pre-pass;
2. the prepared-read resolution;
3. a dense low-four-bit tag switch; and
4. the single CQ-head commit.

Before any callback, copy `user_data`, `res`, and `flags` from the CQE into
locals. A callback can stage work and touch broad process state; it must not
leave later logic dependent on repeated shared-CQ loads. The second sequential
scan costs almost nothing because the small CQ batch is already resident, and
it removes milliseconds of possible WAMR work from the Wayland critical path.

Retain the aligned pointer tag and dense `switch`. At 64 non-reused request
slots, a hash table, dynamic completion object, or indirect handler vtable adds
lookup/allocation/branch-predictor work without solving an identity problem.
Mark setup, registration, fallback, logging, and fatal-error routines
`[[gnu::cold, gnu::noinline]]`; do not force-inline the large state machines
into the CQ loop. This is the useful instruction-cache split: inline the ring
mechanics, outline policy and errors.

## 6. Capacity proof and batching policy

### 6.1 SQ = 256

In a normal turn, a live request can stage at most three SQEs:

- periodic file: timeout + read;
- known-address reconnect: timeout + socket + connect;
- stream: one receive plus at most one send;
- installed-resource teardown: fixed-fd cancel + close;
- unproven-creator teardown phase: exact cancel + fixed-fd cancel; and
- callback-generated work: bounded by one in-flight/staged send per stream.

At 64 requests the conservative three-SQE bound gives 192. The shared deadline
scheduler contributes at most one add/update/remove control, not one timeout
per userspace-only timer. Add Wayland, sd-bus, inotify, and cancellation
headroom and the turn remains below about 224. The next power-of-two size is
256.

That proof is what permits the no-recursive-enter rule. If code growth can
stage more than 256 in one turn, the correct response is a bounded pending
work queue and another phase, not an enter from the middle of CQ iteration.

### 6.2 CQ = default 512

The kernel's default CQ is twice SQ. Keep it. Do not set `CQSIZE` to shrink it.

Steady state produces at most roughly two visible completions per request, and
the file chain produces one. In an adversarial teardown, mutually exclusive
state machines bound old target CQEs plus failed cancel/close controls at about
four per request: 256 across the full pool, still leaving half the CQ for
watchers and remote/control bursts. Successful creator and cleanup controls
are skipped.

On 4 KiB pages the mappings are only about:

- 16 KiB for 256 64-byte SQEs; and
- roughly 12 KiB for the ring header plus 512 16-byte CQEs.

Saving a page is not worth reducing burst tolerance. With
`IORING_FEAT_NODROP` the kernel can use an overflow list, but overflow adds
work and allocation pressure; the design should make it unreachable.

### 6.3 One enter boundary

Normal operation performs:

- no syscall while staging;
- one enter to publish, run deferred task work, and sleep;
- immediate additional enters only for a nonnegative zero-CQE return or a
  kernel short-submit; and
- no enter from a CQ handler.

A worker-result/configuration turn may perform a cold registered-ring
`REGISTER_FILES_UPDATE`, ordinary-fd close, or state swap before
`wl_display_prepare_read`. Those explicit control operations are not hidden in
CQ iteration and do not change the steady-state one-enter boundary.

The enter return value is not a completion count. Drive the loop from the CQ
snapshot. A short submission can skip the wait, and a successful submission
can mask a later wait error in the syscall result.

## 7. Exhaustive setup-flag decision for Linux 6.18

| Setup flag | Decision | Project-specific reason |
|---|---|---|
| `IORING_SETUP_IOPOLL` | reject | Block-device polling; incompatible with this poll/socket/pseudo-file reactor |
| `IORING_SETUP_SQPOLL` | reject | Adds a kernel thread, cache handoff, idle burn; kernel rejects it with `DEFER_TASKRUN` |
| `IORING_SETUP_SQ_AFF` | reject | Meaningful only with rejected SQPOLL |
| `IORING_SETUP_CQSIZE` | omit | Default 512 is the correct burst-safe size |
| `IORING_SETUP_CLAMP` | omit | A fixed 256 request is valid; silent clamping would hide a configuration error |
| `IORING_SETUP_ATTACH_WQ` | reject | There should be no io-wq to share |
| `IORING_SETUP_R_DISABLED` | omit | No restrictions/personality setup requires a disabled phase |
| `IORING_SETUP_SUBMIT_ALL` | **set** | One malformed SQE must not strand later independent requests in the published batch |
| `IORING_SETUP_COOP_TASKRUN` | omit | It changes `ctx->notify_method` for normal task work, while `DEFER_TASKRUN` uses the separate local-work list/wake path |
| `IORING_SETUP_TASKRUN_FLAG` | omit | Adds shared SQ-flag polling; every wait already enters with `GETEVENTS` |
| `IORING_SETUP_SQE128` | reject | No operation needs command extension bytes; doubles SQE footprint |
| `IORING_SETUP_CQE32` | reject | No operation needs 32-byte CQEs; doubles CQ bandwidth |
| `IORING_SETUP_SINGLE_ISSUER` | **set** | Exactly matches the reactor ownership model and is required by `DEFER_TASKRUN` |
| `IORING_SETUP_DEFER_TASKRUN` | **set** | Completion publication and task work run at explicit issuer enter points; enables lockless CQ |
| `IORING_SETUP_NO_MMAP` | reject | Pins user pages long-term and complicates setup for no hot-path gain |
| `IORING_SETUP_REGISTERED_FD_ONLY` | reject | Requires `NO_MMAP`, removes the real fd needed by blind MSG_RING, and saves only cold setup work |
| `IORING_SETUP_NO_SQARRAY` | **set** | Removes SQ index-array memory, load, bounds validation, and associated static-key path |
| `IORING_SETUP_HYBRID_IOPOLL` | reject | Valid only with rejected IOPOLL |
| `IORING_SETUP_CQE_MIXED` | reject | No 32-byte producer; adds CQ stride/flag complexity |

The four selected bits are therefore final, not merely a starting set.

In this exact kernel:

- `DEFER_TASKRUN + SINGLE_ISSUER` sets `ctx->task_complete`;
- `task_complete` sets `ctx->lockless_cq`; and
- completions for this ring are routed to issuer-local work rather than being
  posted from arbitrary completion contexts.

That is precisely the concurrency model the bar needs.

## 8. Exhaustive enter-flag decision

| Enter flag | Decision | Reason |
|---|---|---|
| `IORING_ENTER_GETEVENTS` | **always set** | Required to run deferred local task work and wait |
| `IORING_ENTER_SQ_WAKEUP` | reject | SQPOLL-only |
| `IORING_ENTER_SQ_WAIT` | reject | SQPOLL-only |
| `IORING_ENTER_EXT_ARG` | omit | Persistent timeout SQEs already model deadlines; avoid per-enter copy and on-stack hrtimer |
| `IORING_ENTER_REGISTERED_RING` | **set after registration** | Resolves the ring through the task's registered-ring array instead of fd-table lookup |
| `IORING_ENTER_ABS_TIMER` | omit | Only affects an EXT_ARG wait timeout, which is not used |
| `IORING_ENTER_EXT_ARG_REG` | omit | Registered wait arguments do not help a reactor already using persistent linked and shared-deadline timeout SQEs |
| `IORING_ENTER_NO_IOWAIT` | profile switch | omit for latency; set for efficiency if `FEAT_NO_IOWAIT` exists |

The latency flags are:

```c
IORING_ENTER_GETEVENTS | IORING_ENTER_REGISTERED_RING
```

The efficiency flags add:

```c
IORING_ENTER_NO_IOWAIT
```

Why not use an EXT_ARG timeout for the shared deadline scheduler? A long
deadline combined with frequent unrelated wakes causes the kernel to
initialize, start, cancel, and destroy an on-stack hrtimer on every enter. A
persistent absolute `IORING_OP_TIMEOUT` is updated only when the earliest
userspace-work deadline changes and survives unrelated wakes.

## 9. Feature-bit policy

Linux 6.18.37 advertises all feature bits listed below, but initialization
should still validate the contract explicitly.

| Feature | Policy |
|---|---|
| `IORING_FEAT_SINGLE_MMAP` | require; one rings mapping |
| `IORING_FEAT_NODROP` | require; a full CQ must not silently discard CQEs |
| `IORING_FEAT_SUBMIT_STABLE` | assumed on target; pointer lifetime still extends through consumption/completion as documented per op |
| `IORING_FEAT_RW_CUR_POS` | unused; all project reads use explicit offset 0 or stream position |
| `IORING_FEAT_CUR_PERSONALITY` | unused |
| `IORING_FEAT_FAST_POLL` | require; central to sockets, pipes, PTY, inotify |
| `IORING_FEAT_POLL_32BITS` | validate for full masks; project masks fit low bits but target has it |
| `IORING_FEAT_SQPOLL_NONFIXED` | irrelevant |
| `IORING_FEAT_EXT_ARG` | present but intentionally unused |
| `IORING_FEAT_NATIVE_WORKERS` | irrelevant because the hot workload must not enter io-wq |
| `IORING_FEAT_RSRC_TAGS` | omit; direct-created file nodes cannot be tagged, target CQEs are already required for buffer retirement, and tags would add a nonuniform extra teardown CQE |
| `IORING_FEAT_CQE_SKIP` | require; creator/cleanup/link design depends on it |
| `IORING_FEAT_LINKED_FILE` | require for direct-open/read and socket/connect chains |
| `IORING_FEAT_REG_REG_RING` | require for registered-index registration calls |
| `IORING_FEAT_RECVSEND_BUNDLE` | reject for this workload |
| `IORING_FEAT_MIN_TIMEOUT` | unused; no enter-time coalescing policy |
| `IORING_FEAT_RW_ATTR` | unused |
| `IORING_FEAT_NO_IOWAIT` | required only for the optional efficiency profile |

Also register an `IORING_REGISTER_PROBE` at cold initialization and require the
opcodes used by the selected build. At minimum:

- `TIMEOUT` and `TIMEOUT_REMOVE`;
- `ASYNC_CANCEL`;
- `READ` and `WRITE`;
- `OPENAT2` and `CLOSE`;
- `SOCKET`, `CONNECT`, `RECV`, and `SEND`;
- `POLL_ADD` and `POLL_REMOVE`; and
- `MSG_RING`, in addition to actually exercising the blind registration path.

Blind MSG_RING support is determined by the registration opcode on this fixed
target; a fallback build may retain eventfd only if that operation is absent.

## 10. Exhaustive common SQE-flag decision

| SQE flag | Decision |
|---|---|
| `IOSQE_FIXED_FILE` | set for every operation consuming a fixed-table slot |
| `IOSQE_IO_DRAIN` | reject; it is a global in-flight ordering barrier and conflicts with CQE-skip support |
| `IOSQE_IO_LINK` | use only for timeout->read and safe direct-creator->consumer chains |
| `IOSQE_IO_HARDLINK` | reject; continuing after a failed dependency is wrong for these chains |
| `IOSQE_ASYNC` | reject; it forces io-wq, exactly the opposite of the design |
| `IOSQE_BUFFER_SELECT` | reject; no provided-buffer group |
| `IOSQE_CQE_SKIP_SUCCESS` | use only when success carries no userspace state |

Valid `CQE_SKIP_SUCCESS` uses:

- linked timer head before a periodic read or known-address reconnect;
- direct open/socket creator head when its linked consumer is the useful
  completion;
- fixed-slot close where only failure matters;
- successful poll/timeout update/remove control operations when a state
  machine handles their possible failure CQEs.

Invalid uses:

- receive/read, because byte count and buffer lifetime are required;
- send/write, because the buffer cannot be retired without completion;
- connect, because queued pre-connect sends need the success transition;
- multishot operations, where the kernel rejects the combination; and
- any operation whose success chooses or returns a dynamic slot.

`SUBMIT_ALL` does not make a malformed SQE safe. Every prep helper must zero the
SQE and fill the exact opcode field aliases.

## 11. SQ, CQ, and registration flag words

### 11.1 SQ ring flags

| Shared SQ flag | Policy |
|---|---|
| `IORING_SQ_NEED_WAKEUP` | ignore; SQPOLL is absent |
| `IORING_SQ_CQ_OVERFLOW` | read for diagnostics after a batch |
| `IORING_SQ_TASKRUN` | ignore; `TASKRUN_FLAG` is absent and every wait uses `GETEVENTS` |

No full barrier associated with `NEED_WAKEUP` is needed because there is no SQ
polling thread.

### 11.2 CQ ring flags

`IORING_CQ_EVENTFD_DISABLED` is irrelevant because no completion eventfd is
registered.

### 11.3 Resource registration

Use:

- `IORING_REGISTER_FILES2` with `IORING_RSRC_REGISTER_SPARSE`;
- `IORING_REGISTER_FILES_UPDATE` for real fds installed into known slots;
- `IORING_REGISTER_RING_FDS` once;
- `IORING_REGISTER_USE_REGISTERED_RING` ORed into every later eligible
  registration opcode, passing the registered ring index; and
- `IORING_REGISTER_SEND_MSG_RING` with fd `-1` from DNS workers.

Do not register:

- data buffers;
- provided-buffer rings;
- a completion eventfd;
- NAPI busy-poll;
- an alternate clock;
- io-wq affinity or worker limits;
- personalities;
- restrictions; or
- user memory regions for `NO_MMAP`.

Use the real fd to unregister its registered-ring slot during teardown, then
unmap and close. A registered ring slot holds a file reference; failing to
unregister it can keep the ring and every fixed file alive until task exit.
The registered index is task-local state: only the issuer uses it for enter and
eligible register calls. DNS workers use the blind fd `-1` registration ABI
with the shared real ring fd inside the message SQE; they never use the
issuer's registered index.

### 11.4 Exhaustive registration-opcode policy

Linux 6.18.37 exposes registration opcodes 0 through 35. The complete
project-specific decision is:

| Registration opcode(s) | Decision |
|---|---|
| `REGISTER_BUFFERS`, `UNREGISTER_BUFFERS`, `REGISTER_BUFFERS2`, `REGISTER_BUFFERS_UPDATE`, `REGISTER_CLONE_BUFFERS` | reject; no pinned data-buffer table or clone source |
| `REGISTER_FILES` | reject the legacy dense initializer; use sparse `FILES2` |
| `REGISTER_FILES2` | **use once** with `RSRC_REGISTER_SPARSE` and the full explicit slot count |
| `REGISTER_FILES_UPDATE` | **use** for known real fds; batch adjacent watcher slots during cold setup |
| `REGISTER_FILES_UPDATE2` | omit; no resource tags are consumed |
| `UNREGISTER_FILES` | **use at final ring teardown** after workers are quiesced; ordinary reusable resources still retire through target CQEs before slot reuse |
| `REGISTER_EVENTFD`, `REGISTER_EVENTFD_ASYNC`, `UNREGISTER_EVENTFD` | reject; the reactor itself waits, and DNS uses blind MSG_RING |
| `REGISTER_PROBE` | **use after setup** to require every selected SQE opcode |
| `REGISTER_PERSONALITY`, `UNREGISTER_PERSONALITY` | reject; one issuer credential domain |
| `REGISTER_RESTRICTIONS`, `REGISTER_ENABLE_RINGS` | reject; no disabled setup/sandbox policy requires them |
| `REGISTER_IOWQ_AFF`, `UNREGISTER_IOWQ_AFF`, `REGISTER_IOWQ_MAX_WORKERS` | reject; io-wq is not a normal execution resource, and a zero max value is a query rather than a disable switch |
| `REGISTER_RING_FDS` | **use once** to self-register the ring and obtain the hot enter index |
| `UNREGISTER_RING_FDS` | **use first during ring teardown**, addressed through the retained real fd |
| `REGISTER_PBUF_RING`, `UNREGISTER_PBUF_RING`, `REGISTER_PBUF_STATUS` | reject with provided buffers/multishot receive |
| `REGISTER_SYNC_CANCEL` | reject; issuer-submitted `ASYNC_CANCEL` preserves phase ordering and returns through the same CQ |
| `REGISTER_FILE_ALLOC_RANGE` | reject; every resource has a compile-time explicit fixed slot |
| `REGISTER_NAPI`, `UNREGISTER_NAPI` | reject; no owned high-rate NIC queue |
| `REGISTER_CLOCK` | reject; the default `CLOCK_MONOTONIC` is exactly the module/sd-bus scheduling clock |
| `REGISTER_SEND_MSG_RING` | **use blind from DNS workers**, with syscall fd `-1` |
| `REGISTER_ZCRX_IFQ` | reject with ZCRX/CQE32/registered areas |
| `REGISTER_RESIZE_RINGS` | reject; the proved 256/512 sizing is static, and live resize adds mapping handoff state |
| `REGISTER_MEM_REGION` | reject; no `NO_MMAP` storage or registered enter-wait arguments |
| `REGISTER_QUERY` | **use blind before setup** as a cold UAPI/runtime-mask assertion |

`REGISTER_QUERY` is new enough to be worth using in a raw Linux 6.18 backend.
Issue it with fd `-1`, `nr_args=0`, and an `IO_URING_QUERY_OPCODES` entry from
`<linux/io_uring/query.h>`. Require the selected setup, enter, and common SQE
bits in its returned masks before creating the ring. It reports opcode counts,
not per-opcode runtime support, so it complements rather than replaces
`REGISTER_PROBE` and the post-setup `p.features` checks.

### 11.5 Resource and registration subflags

| Flag/sentinel | Decision |
|---|---|
| `IORING_RSRC_REGISTER_SPARSE` | **set** for the initial fixed-file table |
| `IORING_REGISTER_FILES_SKIP` | omit; initial real watcher fds are adjacent and later updates target exact individual slots |
| `IORING_FILE_INDEX_ALLOC` | reject; kernel-selected slots would add returned-index/generation state |
| `IORING_REGISTER_USE_REGISTERED_RING` | **set** on each eligible post-registration call; never add it to blind fd `-1` calls |
| `IORING_REGISTER_SRC_REGISTERED`, `IORING_REGISTER_DST_REPLACE` | reject with buffer cloning |
| `IORING_REG_WAIT_TS` | reject with registered enter-wait arguments |

## 12. CQE flags: all bits the dispatcher must understand

| CQE flag | Project action |
|---|---|
| `IORING_CQE_F_BUFFER` | impossible in selected design; assert/log if seen |
| `IORING_CQE_F_MORE` | required on each live Wayland-input multishot CQE; impossible for every other selected request |
| `IORING_CQE_F_SOCK_NONEMPTY` | omit `POLL_FIRST` on the next socket receive |
| `IORING_CQE_F_NOTIF` | impossible because SEND_ZC is rejected |
| `IORING_CQE_F_BUF_MORE` | impossible without incremental provided buffers |
| `IORING_CQE_F_SKIP` | impossible without mixed CQE wrap gaps; ignore safely if encountered |
| `IORING_CQE_F_32` | impossible without mixed/32-byte CQ mode; assert/log |

Always copy/read `cqe->flags` before invoking a handler. The current dispatcher
only copies `user_data` and `res` and therefore discards useful socket state
and the evidence needed to reject an unexpected CQE mode.

## 13. Operation-specific decisions

### 13.1 Poll flags

Linux 6.18 multishot poll is edge-triggered: `IORING_POLL_ADD_MULTI` removes
`EPOLLONESHOT`, while the parser still adds `EPOLLET`. Select it only when the
consumer proves that every notification drains the fd to `EAGAIN`.

Wayland input satisfies that proof in the exact linked libwayland 1.25.0:
`wl_display_connect_to_fd()` passes an unlimited maximum to the connection,
the initially 4 KiB input ring grows as required, and `wl_connection_read()`
loops nonblocking `recvmsg()` calls until `EAGAIN`. Use one persistent
multishot `POLLIN` request. A normal input CQE has `CQE_F_MORE`; absence of
`MORE` means the request terminated and must not be assumed armed.

Do not generalize this result. Wayland output is armed only after
`wl_display_flush()` returns `EAGAIN`; keep that watcher conditional and
one-shot so an empty output queue has no persistent writable watcher. sd-bus
changes both its event mask and timeout contract, so recompute and rearm its
one-shot after processing to zero. The fallback inotify poll/read design lacks
a drain proof and is rejected anyway in favor of one direct read.

Rearming a one-shot poll re-evaluates current readiness and therefore safely
completes immediately if a conditional source remains ready.

`IORING_POLL_UPDATE_EVENTS` is useful on `POLL_REMOVE` for changing the
one-shot sd-bus mask in place. `IORING_POLL_UPDATE_USER_DATA` is unnecessary
if the bus poll token is stable.

Do **not** use `IORING_POLL_ADD_LEVEL` on Linux 6.18.37. The UAPI defines it and
the event parser knows how it would alter edge behavior, but
`io_poll_add_prep()` rejects every add flag except `POLL_ADD_MULTI`. This is a
header/implementation inconsistency in the target snapshot, not a supported
feature.

Poll completions can contain `ERR`, `HUP`, `NVAL`, and `RDHUP` even if only
`POLLIN`/`POLLOUT` was requested. State machines must handle terminal bits,
not test only the requested bit.

### 13.2 Timeout flags

| Timeout flag | Use |
|---|---|
| `IORING_TIMEOUT_ABS` | the shared userspace-work deadline timer |
| `IORING_TIMEOUT_UPDATE` | update that timer via `TIMEOUT_REMOVE` |
| `IORING_TIMEOUT_BOOTTIME` | reject; bar intervals and sd-bus use monotonic |
| `IORING_TIMEOUT_REALTIME` | reject; wall-clock adjustments must not alter scheduling |
| `IORING_LINK_TIMEOUT_UPDATE` | reject; no link-timeout operation |
| `IORING_TIMEOUT_ETIME_SUCCESS` | use on expected timer expiry, especially linked timer heads |
| `IORING_TIMEOUT_MULTISHOT` | reject for every selected timer class |

`ETIME_SUCCESS` does not change `cqe.res`: expiry remains `-ETIME`. It prevents
the request from being marked failed, which preserves a normal link and
allows a skipped timer head to remain skipped.

Use one absolute `CLOCK_MONOTONIC` timeout for every deadline whose expiry must
return to userspace before useful work can begin: pure WAMR timers, exec
respawn, unresolved-DNS retry, and the sd-bus deadline. Keep the deadline of at
most one such action per request slot in a contiguous `uint64_t[64]`, plus a
64-bit active mask and one scalar bus deadline. A bit scan over the active
slots is bounded, allocation-free, and more cache-predictable at `N <= 64`
than a pointer heap or tree.

The shared timeout has one stable token. Add it with
`ABS | ETIME_SUCCESS`, update it with `TIMEOUT_REMOVE(UPDATE | ABS)` whenever
the earliest active deadline changes, and remove it when the set becomes
empty. On its terminal CQE, acquire `CLOCK_MONOTONIC` once, run every due
action only after the Wayland pre-pass, and compute the next minimum. A pure
WAMR timer's next deadline is callback-return time plus its interval. That
preserves fixed-delay backpressure: a 3-7 ms callback cannot create a catch-up
queue.

This is not a generic timer heap. The default bar has a 16 ms TTY timer and,
after removing RTC sysfs, a 1 s datetime timer. One shared timeout emits the
same one CQE needed for each distinct wake and coalesces coincident deadlines,
while eliminating redundant live kernel timeout requests. A future increase
to 64 sparse timers still costs at most 64 deadline comparisons, and the
active mask avoids touching inactive slots.

Multishot timeout remains wrong: it imposes fixed-rate behavior, cannot adapt
the next deadline to callback duration, and can post catch-up CQEs. An
enter-time timeout is also wrong because every unrelated wake destroys and
recreates its on-stack hrtimer.

The periodic file chain remains:

```text
TIMEOUT(relative, ETIME_SUCCESS, IO_LINK, CQE_SKIP_SUCCESS)
    -> READ(fixed file, offset 0)
```

It gives one visible read CQE per normal tick and starts the read in-kernel
without a userspace round trip.

For a reconnect whose sockaddr is already known, use the analogous three-SQE
chain:

```text
TIMEOUT(relative, ETIME_SUCCESS, IO_LINK, CQE_SKIP_SUCCESS)
    -> SOCKET_DIRECT(IO_LINK, CQE_SKIP_SUCCESS)
    -> CONNECT(fixed file)
```

It leaves only the connect CQE on the normal path. An unresolved hostname
cannot use this chain because expiry must first launch the DNS worker; that
retry belongs in the shared userspace-work deadline scheduler.

### 13.3 Send/receive flags

| Flag | Decision |
|---|---|
| `IORING_RECVSEND_POLL_FIRST` | adaptive idle receive only |
| `IORING_RECV_MULTISHOT` | reject; requires buffer selection and complicates WAMR ownership |
| `IORING_RECVSEND_FIXED_BUF` | reject; normal SEND/RECV prep masks in 6.18.37 reject it |
| `IORING_SEND_ZC_REPORT_USAGE` | reject with SEND_ZC |
| `IORING_RECVSEND_BUNDLE` | reject; no provided buffers and messages are tiny |
| `IORING_SEND_VECTORIZED` | reject; the project sends one contiguous ring chunk |

There is an important exact-snapshot trap: although the UAPI comment describes
`IORING_RECVSEND_FIXED_BUF`, the normal `SEND/SENDMSG` and `RECV/RECVMSG` prep
flag masks in `io_uring/net.c` omit it. Setting it on the project's ordinary
operations returns `-EINVAL`. The zero-copy send path accepts it, but that path
is independently unsuitable.

### 13.4 MSG_RING flags

Use command `IORING_MSG_DATA` with no message flags. Do not use
`IORING_MSG_RING_CQE_SKIP`, which is not applicable to data messages, or
`IORING_MSG_RING_FLAGS_PASS`, because the DNS notification needs no synthetic
CQE flags.

### 13.5 Async-cancel flags

| Cancel flag | Decision |
|---|---|
| `IORING_ASYNC_CANCEL_ALL` | use with a fixed-fd match to cancel every active operation on that resource |
| `IORING_ASYNC_CANCEL_FD` | use during every active fixed-resource teardown |
| `IORING_ASYNC_CANCEL_ANY` | reject; an unscoped arbitrary cancellation is unsafe |
| `IORING_ASYNC_CANCEL_FD_FIXED` | use together with `FD` because the SQE fd is a fixed-table index |
| `IORING_ASYNC_CANCEL_USERDATA` | use for a timer/direct-creator/link-head token; it is also the default when no fd/op selector is supplied |
| `IORING_ASYNC_CANCEL_OP` | omit; the request token or fixed resource is a stronger identity |

For a fixed-resource cancellation:

```c
sqe->opcode = IORING_OP_ASYNC_CANCEL;
sqe->fd = fixed_slot;
sqe->cancel_flags = IORING_ASYNC_CANCEL_FD
                  | IORING_ASYNC_CANCEL_FD_FIXED
                  | IORING_ASYNC_CANCEL_ALL;
sqe->flags = IOSQE_CQE_SKIP_SUCCESS;
```

For a linked no-file timer or a direct creator that has not installed its target
slot, place its exact `user_data` token in `sqe->addr` and use
`IORING_ASYNC_CANCEL_USERDATA`. Add `IORING_ASYNC_CANCEL_ALL` if a stable token
can identify more than one live request; unique per-operation tokens need no
`ALL`. Track which state-head token can be live and treat `-ENOENT` as expected
stale control state. Treat `-EALREADY` on an unproven direct creator as a
mandatory retry state, not permission to close its possibly future slot.

Do not set `IOSQE_FIXED_FILE` on the cancel SQE merely because its match key is
a fixed fd; `IORING_ASYNC_CANCEL_FD_FIXED` carries that meaning. For a proven
installed resource, submit the fd cancel before direct close in one staged
batch. For an unproven creator, use the two-phase protocol in Section 14.9.
For an `ALL` cancel, `cqe.res` is a match count and zero is success; never use
that control result to free a buffer. Only the target operation's terminal CQE
clears its locally tracked ownership bit.

### 13.6 Open, socket, connect, and close

- Create files and sockets directly into explicit fixed slots
  (`file_index = slot + 1`).
- Consume slots with `fd = slot` plus `IOSQE_FIXED_FILE`.
- Close a direct slot with `file_index = slot + 1` and a zero `fd`.
- Keep `O_CLOEXEC` off direct creators; a fixed file is not installed in the
  process fd table and the kernel rejects that combination.
- Keep `RESOLVE_CACHED` on asynchronous open. On `-EAGAIN`, perform the
  controlled `O_RDONLY|O_NONBLOCK` fallback and update the fixed slot during
  cold pre-reactor initialization. Never run a potentially blocking path walk
  while a Wayland read is prepared or from the steady-state CQ handler; a
  dynamic reopen that cannot tolerate `-EAGAIN` belongs on the blocking worker
  path.
- For the exact audited proc sampler set, retain `O_NONBLOCK` on the opened
  fixed file; do not generalize that execution-property assertion by prefix.
- Create fixed-only stream sockets as `SOCK_STREAM|SOCK_NONBLOCK`; omit
  `SOCK_CLOEXEC`, which direct installation rejects.
- Link direct open to first read and direct socket to connect as described
  above.

### 13.7 Read/write variants

Use plain `READ` and `WRITE` with fixed files, not `READ_FIXED` or
`WRITE_FIXED`. The latter mean fixed **buffers**, not fixed descriptors.

No buffer table is registered today, so the unused
`uring_prep_read_fixed_slot()` helper would fail if called. Remove it or name it
so this distinction cannot be missed.

Do not use `READ_MULTISHOT`. It requires selected/provided buffers and would
move ownership away from the direct WAMR destination/bounce design.

`IORING_OP_PIPE` does not improve exec startup. The child needs the write end
installed before/at fork, so an asynchronous pipe creation would require a
completion wait and fixed-fd extraction before the process operation. The
synchronous `pipe2()` setup is simpler and not on steady-state I/O.

### 13.8 Exact selected SQE recipes

Every row below starts from the fully zeroed 64-byte SQE. Fields not named in
the row remain zero; that includes every union alias and padding member.

| Operation | Nonzero fields and exact flag policy |
|---|---|
| relative linked-work timeout | `opcode=TIMEOUT`, `addr=&ts`, `len=1`, `timeout_flags=ETIME_SUCCESS`, `flags=IO_LINK|CQE_SKIP_SUCCESS`; use only for timeout->read and known-address timeout->socket->connect |
| absolute shared scheduler timeout | `opcode=TIMEOUT`, `addr=&ts`, `len=1`, `timeout_flags=ABS|ETIME_SUCCESS` |
| timeout update | `opcode=TIMEOUT_REMOVE`, `addr=old_user_data`, `addr2=&new_ts`, `timeout_flags=UPDATE|ABS`; skip the successful control CQE |
| timeout removal | `opcode=TIMEOUT_REMOVE`, `addr=old_user_data`; skip the successful control CQE |
| ordinary fixed-file read | `opcode=READ`, `fd=slot`, `flags=FIXED_FILE`, `addr=buf`, `len=capacity`, `off=0`, `rw_flags=0` |
| ordinary fixed-file write | `opcode=WRITE`, `fd=slot`, `flags=FIXED_FILE`, `addr=buf`, `len=remaining`, `off=0`, `rw_flags=0` |
| direct proc open | `opcode=OPENAT2`, `fd=AT_FDCWD`, `addr=path`, `addr2=&open_how`, `len=sizeof(open_how)`, `file_index=slot+1`; `open_how.flags=O_RDONLY|O_NONBLOCK`, `resolve=RESOLVE_CACHED`; optionally `IO_LINK|CQE_SKIP_SUCCESS` |
| direct socket | `opcode=SOCKET`, `fd=domain`, `off=SOCK_STREAM|SOCK_NONBLOCK`, `len=protocol`, `file_index=slot+1`; optionally `IO_LINK|CQE_SKIP_SUCCESS`; never `SOCK_CLOEXEC` |
| fixed-file connect | `opcode=CONNECT`, `fd=slot`, `flags=FIXED_FILE`, `addr=&sockaddr`, `addr2=addrlen` |
| socket receive | `opcode=RECV`, `fd=slot`, `flags=FIXED_FILE`, `addr=buf`, `len=capacity`, `msg_flags=0`; `ioprio` is either zero or adaptive `POLL_FIRST` |
| socket send | `opcode=SEND`, `fd=slot`, `flags=FIXED_FILE`, `addr=buf`, `len=remaining`, `msg_flags=0`, `ioprio=0`; the kernel adds `MSG_NOSIGNAL` |
| Wayland input poll | `opcode=POLL_ADD`, `fd=wayland_slot`, `flags=FIXED_FILE`, `poll32_events=POLLIN`, `len=POLL_ADD_MULTI`; never combine with `CQE_SKIP_SUCCESS` |
| one-shot fixed poll | `opcode=POLL_ADD`, `fd=slot`, `flags=FIXED_FILE`, `poll32_events=mask`, `len=0` |
| poll mask update | `opcode=POLL_REMOVE`, `addr=old_user_data`, `len=POLL_UPDATE_EVENTS`, `poll32_events=new_mask`; skip the successful control CQE |
| poll removal | `opcode=POLL_REMOVE`, `addr=old_user_data`, `len=0`; skip the successful control CQE |
| direct fixed close | `opcode=CLOSE`, `fd=0`, `file_index=slot+1`; skip success only after cancellation/lifetime state is established |
| cancel by fixed resource | `opcode=ASYNC_CANCEL`, `fd=slot`, `cancel_flags=FD|FD_FIXED|ALL`; do not set `FIXED_FILE`; skip the successful control CQE |
| cancel by exact token | `opcode=ASYNC_CANCEL`, `addr=target_user_data`, `cancel_flags=USERDATA` plus `ALL` only for a shared token; skip the successful control CQE |
| blind DNS notification | syscall fd `-1`, register opcode `REGISTER_SEND_MSG_RING`; embedded SQE has `opcode=MSG_RING`, `fd=real_ring_fd`, `addr=MSG_DATA`, `len=0`, `off=notification_user_data`, and both SQE/message flags zero; a userspace pending bit coalesces the whole result list, which is the authoritative payload |

Do not set `RWF_NOWAIT`. io_uring already performs the initial issue in
nonblocking mode; explicitly setting it converts a readiness retry into a
final `-EAGAIN` for operations that otherwise use fast-poll. Do not set
`MSG_WAITALL`, `MSG_MORE`, or redundant `MSG_NOSIGNAL`: they either delay a
small interactive transfer or add no behavior on this kernel path.

Pointers copied during submission (`timespec`, `open_how`, pathname, and
connect address) must remain valid until the kernel advances SQ head. Data
buffers for read/write/send/recv remain owned until their CQE. Keeping all of
them in non-reused request storage makes both rules structural rather than
callback timing assumptions.

## 14. Subsystem integration

### 14.1 Wayland

The existing high-level sequence is correct:

1. dispatch pending events until empty;
2. repeat `prepare_read` until it succeeds, dispatching pending events between
   attempts;
3. flush requests;
4. wait for readiness;
5. call exactly one of `read_events` or `cancel_read`; and
6. dispatch the queued events.

Check the return from every dispatch/flush call. A negative
`wl_display_dispatch_pending()` is fatal, and a negative
`wl_display_flush()` is recoverable only when `errno == EAGAIN`.

Keep conditional `POLLOUT`. `wl_display_flush()` is nonblocking and returns
`EAGAIN` when buffered output remains. Arm a one-shot output poll only then;
an unconditional `POLLOUT` request would complete continuously.

Arm input once as multishot `POLLIN`. In libwayland 1.25.0,
`wl_display_read_events()` reaches a receive loop that expands its initially
4 KiB, unlimited input ring and reads until `EAGAIN`. That exact behavior makes
Linux 6.18's edge-triggered persistent poll safe. On every input CQE:

- require `IORING_CQE_F_MORE` for the normal live path;
- resolve the outstanding prepared read with `wl_display_read_events()`;
- leave the poll armed rather than staging another SQE; and
- treat a CQE without `MORE` as watcher termination, resolving the prepared
  read and then shutting down or deliberately recreating the watcher.

Pin this assumption to libwayland 1.25 behavior in a contract test. A future
bounded or single-receive backend must use a one-shot input watcher instead.

Pre-scan the CQ batch for Wayland before invoking WAMR/module callbacks and
handle terminal poll bits explicitly.

### 14.2 sd-bus

At the beginning of each reactor turn:

1. call `sd_bus_process(bus, nullptr)` until it returns zero;
2. call `sd_bus_flush(bus)` as needed;
3. obtain the current fd from `sd_bus_get_fd()`;
4. obtain the current event mask from `sd_bus_get_events()`;
5. obtain the absolute monotonic deadline from `sd_bus_get_timeout()`.

Maintain:

- the fd currently installed in the bus fixed-file slot;
- one stable one-shot poll token;
- the currently armed poll mask;
- one scalar deadline entry in the shared userspace-work scheduler; and
- the last absolute deadline returned by sd-bus.

If the bus fd changes, cancel the old poll token and wait for its terminal
target CQE before replacing/reusing the fixed slot or poll token. Fixed-table
replacement alone does not revoke the old request, and immediate token reuse
would make a late old CQE indistinguishable from the new watcher. This fd
transition is cold; a one-enter retirement phase is better than a generation
race. Then install the new fd and arm its wait state.

If the event mask is zero, remove any pending poll. If the poll is absent and
the mask is nonzero, add it. If only its mask changed, issue
`POLL_REMOVE` with `IORING_POLL_UPDATE_EVENTS` and skip the successful update
CQE. If update loses a race and reports `-ENOENT`, mark the old request gone
and add a fresh poll.

Treat `UINT64_MAX` as infinite. If the deadline is finite:

- process the bus again immediately rather than scheduling a deadline if the
  deadline is already due;
- otherwise store it as the bus item and reconcile the one shared absolute
  timeout against the minimum of all active userspace-work deadlines.

If the deadline becomes infinite, deactivate the bus item and reconcile the
shared timeout. Removal or update races on the shared timeout are scheduler
control state, not bus failure.

After either the bus poll or the shared scheduler finds the bus deadline due,
mark the bus processable. Finish the Wayland prepared-read boundary first,
then process sd-bus until zero and recompute both wait dimensions.

### 14.3 Shared userspace-work deadline scheduler

Keep scheduler data out of `IORequest`'s hot completion fields:

- one `alignas(64) uint64_t deadline_ns[64]` indexed by request slot;
- one `uint64_t active_mask` for pure-timer/exec/DNS-retry slots;
- one bus deadline plus active bit;
- one persistent `__kernel_timespec` used by the kernel request; and
- armed, update-pending, and current-deadline state for one stable timeout
  token.

Iterating the set bits gives direct, stable identity without heap positions,
generation counters, allocation, or pointer chasing. Checked arithmetic must
convert sd-bus microseconds and module milliseconds to nanoseconds; saturation
means infinite, never wraparound into an immediate timeout.

Reconcile once at the normal staging boundary after all callbacks have changed
their items. Add when no target exists, update only when the minimum changes,
and remove only when the set becomes empty. A successful update keeps the
target request alive. If an update/remove control reports `-ENOENT` or
`-EALREADY`, do not add a second target speculatively; wait for the old
target's terminal CQE, mark it unarmed, and reconcile again. This preserves
the one-kernel-timeout invariant through expiry races.

On expiry, sample monotonic time, clear `armed`, and select all due items before
calling any of them. Run sd-bus and WAMR actions only after the Wayland
pre-pass. A WAMR item is invoked at most once in this turn and receives its
next deadline only after its callback returns. If callback work makes another
item overdue, service that bounded item set before sleeping again; never
synthesize missed fixed-rate ticks.
Recheck the active bit and operation generation before each selected callback,
because an earlier callback can quarantine or supersede another due item.

### 14.4 inotify

Register the inotify fd in a fixed slot and allocate one persistent 4 KiB host
buffer with `alignas(struct inotify_event)`. Page alignment has no benefit for
this copied, non-direct read. Keep exactly one `READ` outstanding. On
completion:

- `res > 0`: validate record boundaries, parse every record, set the reload
  bit, and stage another read;
- `res == -EAGAIN` should not normally become a final CQE when fast-poll is
  available; rearm if it does;
- terminal errors disable/recreate the watch as appropriate.

Do not use a stack buffer because the kernel owns the read destination until
completion. Do not call `read(2)` after a poll CQE.

### 14.5 Blocking-service completion

Workers must never:

- obtain an SQE;
- update SQ/CQ words;
- call enter on the single-issuer ring; or
- invoke request state machines.

They may:

1. resolve DNS, spawn a child, or read/parse a config snapshot in their
   declared lane;
2. append a tagged, host-owned completion record under the result mutex;
3. call blind `REGISTER_SEND_MSG_RING` with the real ring fd embedded in the
   message SQE.

Use one mutex-protected `notification_pending` bit to coalesce every worker
lane. A worker appends its result and changes false->true under the queue lock;
only that transition sends a blind MSG_RING. On its CQE, the issuer takes the
same lock, detaches the entire heterogeneous result list, and clears the bit
before unlocking. A worker that arrives after the detach then observes false
and sends the next wake, so there is neither a lost wake nor one kernel request
allocation per result.

The worker CQE handler only detaches records into an issuer-owned pending
cold-work list. At the top of the next turn, before `wl_display_prepare_read`,
the issuer dispatches each tag: DNS continues a socket state machine, spawn
installs the still-open real fd into the request's fixed slot, and reload swaps
a successfully parsed config snapshot plus acquired byte blobs before applying
Wayland/WAMR changes. This keeps registration syscalls and cold allocations out
of CQ iteration. A stale/dead destination closes or frees the record's
resources without invoking the old logical request.

Each job/result carries a per-request operation generation (and reloads carry
a reload generation). Non-reused request storage prevents pointer aliasing,
but it does not by itself distinguish a late DNS/spawn attempt from a newer
attempt on the same logical request. Accept a result only when both pointer and
generation match the issuer-owned state.

If the blind registration syscall fails before delivering, the sending worker
reacquires the lock, clears the bit, and retries notification while work
remains; classify `EINTR`/transient allocation failure separately from a dead
target. Make every worker lane joinable or reference-counted. Set the shutdown
gate, join/quiesce all of them, and only then unregister/close the real ring
fd—an fd-number reuse race is otherwise possible even though request storage
is non-reused.

### 14.6 procfs sampling and wall-clock time

Keep the kernel-linked timer/read design and offset zero. For pinned WAMR
memory the kernel may copy directly into the stable guest mapping. For a
growable memory, the persistent host bounce buffer remains necessary and the
guest address must be freshly revalidated after completion.

The current broad "/proc except PIDs" prefix test is not a proof that an
arbitrary module-selected proc file cannot block. The built-in workload uses
three known pseudo-files. For a hard execution-property guarantee, expose
trusted sampler IDs or an exact host allowlist for:

- `/proc/stat`;
- `/proc/meminfo`;
- `/proc/loadavg`.

Extend that list only after tracing the exact file operations. A namespace
prefix is not an execution-property type.

Open those fixed-only sampler files with `O_RDONLY|O_NONBLOCK` plus
`RESOLVE_CACHED`. On this exact target the nonblocking bit makes io_uring cache
the file as nowait-capable and avoids a redundant `vfs_poll()` readiness probe
on each read. This specialization is valid only for the audited proc entries;
do a controlled path-lookup fallback with the same `O_NONBLOCK` bit on a cache
miss and keep the sampler allowlist closed. Complete that fallback, direct
installation, and the first linked read for all built-ins before entering the
steady-state Wayland loop, preallocating their seq-file buffers outside
latency-sensitive operation. A later reopen that misses the dcache must defer
to the declared blocking-worker path instead of blocking the issuer.

Do not include RTC sysfs in that allowlist. Drive the datetime module from the
shared userspace-work deadline scheduler and obtain realtime seconds from a
vDSO-backed host import. A physical RTC query, if required, is explicitly
worker-backed and publishes a coalesced result through the same
worker-notification path as other blocking jobs.

### 14.7 sockets

Keep direct socket creation, fixed slots, fast-poll connect, one receive, and
one send in flight. Use socket->connect for the initial attempt and
timeout->socket->connect for a known-address retry. An unresolved-DNS retry
uses the shared userspace-work scheduler because it must launch a worker.
Apply adaptive receive `POLL_FIRST` from `SOCK_NONEMPTY`.

On EOF, connect failure after installation, or another terminal error, enter
the common cancel->close->retire phase. Do not stage the reconnect chain until
an old in-flight send/receive CQE can no longer mutate the request state.

Do not use multishot receive: selected buffers would force host-buffer
management and another copy/translation policy for WAMR, while the existing
one-request receive naturally implements protocol backpressure.

Do not use send zero-copy. Project writes are at most 1 KiB queue chunks and
often below a cache line. SEND_ZC introduces notification request state, a
second notification CQE, page accounting/pinning decisions, and buffer
lifetime complexity. It cannot amortize those fixed costs here.

### 14.8 PTY and exec streams

Keep ordinary `READ`/`WRITE` on fixed slots. These descriptors are naturally
bursty and often already contain bytes when rearmed, so an up-front poll-first
strategy would add work.

The static outbound ring remains the buffer-lifetime boundary. A send/write
CQE is required before retiring or rewriting its bytes; never skip it.
On EOF/child exit, cancel a possible sibling write/read before direct close
and delay respawn until both ownership bits are clear. Exec respawn then uses
the shared userspace-work scheduler because expiry must return to userspace to
enqueue a spawn-worker job; it cannot benefit from an in-kernel link. Initial
PTY creation uses the same spawn lane, so hotplugged monitor setup never forks
on the issuer.

### 14.9 Resource teardown and module quarantine

Every socket/PTY/exec close is a bounded cancellation phase, not "close the
slot and hope every source wakes":

1. enter a closing state; quarantine additionally marks the logical request
   dead so no target CQE can rearm it;
2. deactivate any shared-scheduler item and cancel every possibly live linked
   timer/creator head by its exact user-data token;
3. if the resource is proven installed, cancel active operations by fixed fd
   with `FD|FD_FIXED|ALL`, then stage direct close in the same batch;
4. if a direct creator may still be unbound, cancel its exact token and issue
   an opportunistic fd cancel, but withhold close; retry `-EALREADY`, and only
   after creator failure/cancellation or exact-cancel `-ENOENT` issue the
   second fd cancel followed by close;
5. let every target's terminal CQE release kernel-owned buffer state;
6. when a skipped-success link head fails/cancels, retire its silently skipped,
   never-issued dependents from the head CQE rather than waiting for a CQE that
   cannot exist;
7. retain non-reused slot identity and kernel-referenced storage;
8. only after every locally tracked old operation retires, arm reconnect or
   release the quarantined instance.

Final process shutdown is a separate, non-reuse boundary. Stop and join every
worker, drop the task-local registered-ring reference, unregister the fixed
table through the retained real fd, then unmap and close the ring. The final
ring release cancels any process-lifetime watcher still in flight; its request
records, timespecs, receive buffers, and send rings remain valid until process
exit. A disconnected or already-broken reactor must not depend on receiving a
new CQE merely to make shutdown memory-safe.

Because cancellation is issued by the reactor, it preserves
`SINGLE_ISSUER`. It does not need the synchronous registration cancellation
API and does not create an io-wq worker for the selected poll/timer operations.

## 15. Fixed files, buffers, and memory mappings

### 15.1 Fixed files: keep

The sparse table provides:

- direct indexing for every hot operation;
- no per-operation process fd-table lookup;
- stable ownership across async operations;
- direct open/socket installation; and
- direct close.

After replacing eventfd, the required slots are 64 request slots plus Wayland,
sd-bus, and inotify. Keeping one spare slot is harmless if it simplifies
constants.

Do not add file-resource tags as the teardown barrier. A tag CQE is emitted
only when the old resource node's final reference disappears, which is a useful
debug assertion, but direct `OPENAT2`/`SOCKET` installation has no tag field.
Tagging only real-fd update paths would create two lifetime protocols, and the
read/recv/send/write CQEs are still necessary to retire their buffers. The
uniform explicit ownership-bit protocol is both cheaper and applicable to
every slot; an optional debug build may tag eligible watcher/PTY installs to
cross-check it.

### 15.2 Registered data buffers: reject

Registered buffers do not create zero-copy for procfs, sysfs, sockets, pipes,
or PTYs. Those sources still copy bytes. Registration would:

- pin/account pages;
- complicate WAMR `memory.grow`;
- require a fixed lifetime for every buffer;
- add setup/teardown state; and
- save only a small buffer-import path at very low operation rate.

The growable-WAMR bounce policy and pinned-WAMR direct policy are already the
right ownership split.

### 15.3 Provided buffers and buffer rings: reject

They are valuable for high-rate multishot networking with a buffer pool. Here
they would replace a direct guest destination with host buffers, buffer IDs,
recycling, partial-consumption flags, and guest copies. That increases both
latency and state.

### 15.4 ZCRX and NAPI: reject

Linux 6.18 zero-copy receive requires supported NIC queue ownership, registered
memory areas, refill/completion rings, and 32-byte CQEs. It does not apply to
Wayland, sd-bus, inotify, procfs, PTYs, pipes, or Unix-domain MPD, and remote
MPD traffic is far too small to justify it.

NAPI busy-poll similarly targets NIC packet latency at the cost of CPU. Most
of this ring's descriptors never touch a NIC.

### 15.5 Kernel-owned mmap: keep and harden for fork

Keep the normal two mappings:

- one `SINGLE_MMAP` rings mapping; and
- one SQE mapping.

Do not use `NO_MMAP`. The kernel-owned path allocates/maps the small regions
eagerly. User-provided ring memory requires long-term page pins and can invoke
more complex mapping paths without reducing the enter contract.

Immediately after successful mapping, apply each advice separately
(`madvise()` takes one advice value, not an ORed flag mask):

```c
if (madvise(ring_mem, ring_sz, MADV_DONTFORK) < 0 ||
    madvise(ring_mem, ring_sz, MADV_DONTDUMP) < 0)
    fail_setup();
if (madvise(sqe_mem, sqe_sz, MADV_DONTFORK) < 0 ||
    madvise(sqe_mem, sqe_sz, MADV_DONTDUMP) < 0)
    fail_setup();
```

WayRing forks PTY/exec children after ring initialization. The io_uring mmap
path sets `VM_DONTEXPAND` but not `VM_DONTCOPY` or `VM_DONTDUMP`.
`MADV_DONTFORK` prevents pointless child VMA/page-table inheritance and
`MADV_DONTDUMP` keeps shared kernel-ring contents out of core dumps.

Do not add `MAP_POPULATE`; the kernel mapping inserts its pages at mmap time.

## 16. Proving io-wq stays out of the hot path

`DEFER_TASKRUN` is not "no worker threads." An operation that cannot proceed
nonblocking can still be punted to io-wq.

The selected hot operations remain inline/fast-poll:

- poll and timeout are native async operations;
- socket send/recv/connect use nonblocking issue then fast-poll;
- pipe and PTY files advertise/use nonblocking behavior;
- inotify has poll plus nonblocking read;
- the selected procfs files are individually admitted, opened nonblocking,
  prewarmed, and have no contending per-open seq-file reader;
- fixed close and fixed file-table operations are native.

Never set `IOSQE_ASYNC`.

This is the selected execution path, not an impossible-to-break promise under
kernel memory exhaustion. In Linux 6.18, if internal fast-poll arming returns
`IO_APOLL_ABORTED` (for example because its poll-state allocation fails),
`io_queue_async()` falls back to io-wq. No SQE flag converts every such internal
failure into a guaranteed userspace `-EAGAIN` while retaining transparent
fast-poll retry.

Make that boundary operationally explicit:

- arm the maximum expected concurrent poll/read/recv set during cold startup,
  so request and async-poll caches reach steady-state before rendering;
- trace `io_uring:io_uring_queue_async_work` and treat any hit as degraded-mode
  violation, not normal scheduling; and
- keep every underlying stream descriptor `O_NONBLOCK`, limiting the damage if
  the kernel does take that exceptional fallback.

Thus "no io-wq" is a proven normal-path architecture plus a monitored resource
invariant. Claiming an absolute guarantee across kernel allocation failure
would be false without changing the kernel or replacing transparent fast-poll
with a more expensive explicit-poll protocol.

This does not mean "no process threads." DNS, spawn, config read/parse, and an
optional physical-RTC query are explicitly worker-backed because their APIs
can block. Name those lanes distinctly and keep their queues/results visible
in diagnostics. The invariant is that the issuer and io_uring's implicit
worker pool never absorb undeclared blocking work.

Ordinary disk files must not enter the timer-poll ABI. A cold buffered read can
eventually require io-wq, page wait retry, or storage I/O. If disk-backed
module input is ever added, it belongs in a separate explicitly worker-backed
subsystem, not in this latency ring under a false single-thread claim.

Likewise, sysfs is not an execution class. The RTC attribute reaches a driver
callback and is excluded even though kernfs reports it readable. The
no-io-wq check alone is insufficient here: tracing must also show that no
selected issuer-side operation sleeps in an unbounded hardware callback.

Ways to validate the invariant without a throughput benchmark:

- inspect `/proc/<pid>/task` after every operation class is exercised;
- trace `io_uring:io_uring_queue_async_work`;
- inspect ring fdinfo and worker creation;
- fault-in and evict candidate file paths during a dedicated correctness test;
- make the host reject unclassified path sources.

## 17. Latency profile versus efficiency profile

### Latency profile: the answer for this session

```c
enter_flags = IORING_ENTER_GETEVENTS
            | IORING_ENTER_REGISTERED_RING;
```

When requests remain pending, Linux 6.18 marks the sleeping reactor as
`in_iowait`. On schedutil, a wake can initiate an I/O-wait boost and repeated
nearby wakes can increase it. Intel pstate has a comparable path. This can
reduce the CPU-frequency ramp delay before Wayland parsing, WAMR callbacks,
layout, and rendering.

### Efficiency profile

```c
enter_flags = IORING_ENTER_GETEVENTS
            | IORING_ENTER_REGISTERED_RING
            | IORING_ENTER_NO_IOWAIT;
```

This avoids classifying the sleep as I/O wait. It is suitable when power and
idle behavior matter more than the last wake-to-render latency, or when the
machine is already on a fixed/performance governor and the boost has no value.

This is the only selected io_uring flag whose winner legitimately depends on
machine power policy. Make it a named configuration/profile and report it in
diagnostics.

The inspected development host currently reports `amd-pstate-epp` with both
the governor and energy-performance preference set to `performance`. It does
not use the schedutil or intel_pstate boost paths audited above, so the two
enter profiles are not expected to differ materially in frequency response on
that host today. Keep the latency profile as the stated default because power
policy is mutable deployment state; a deployment that permanently pins a
performance policy may select `NO_IOWAIT` without giving up that unavailable
boost.

## 18. Why the attractive alternatives lose

### SQPOLL

- rejected by the kernel with `DEFER_TASKRUN`;
- needs a kthread and SQ ownership handoff;
- burns or periodically wakes CPU while the bar is idle;
- helps submission-heavy workloads, not one submission batch per external
  wake.

### IOPOLL / HYBRID_IOPOLL

- aimed at polled direct block I/O;
- project fds are pollable streams, pseudo-files, timers, and IPC;
- incompatible opcodes would fail setup/issue.

### Multiple rings

- require another wait/wake integration mechanism;
- split one total order into cross-ring notification;
- duplicate mappings/fixed tables;
- cannot improve a single issuer handling all callbacks.

### Multishot poll for every watcher

- Linux 6.18 implements it as edge-triggered persistent poll, so each consumer
  needs a drain-to-`EAGAIN` proof;
- libwayland 1.25.0 supplies that proof for Wayland input, which is why that
  one watcher is selected as multishot;
- Wayland output is conditional on buffered output, while sd-bus changes its
  requested mask and has a separate absolute deadline; and
- one-shot rearm is level-safe for those cold, conditional/dynamic cases and
  costs no extra enter because handlers only stage the next SQE.

Conversely, keeping Wayland input one-shot leaves a poll removal and insertion
on every compositor-input wake despite the exact library now draining to
`EAGAIN`. It is the one place where multishot removes recurring kernel work
without adding buffer ownership or fairness state.

### COOP_TASKRUN and TASKRUN_FLAG

- `DEFER_TASKRUN` routes this ring's completions through
  `io_req_local_work_add()`, not the normal task-work path whose
  `ctx->notify_method` is changed by `COOP_TASKRUN`;
- local work wakes the issuer only when its current CQ-wait demand requires it
  and is executed at the explicit get-events boundary; and
- `TASKRUN_FLAG` would add shared SQ-flag traffic even though the loop always
  enters to wait.

### Registered-only ring fd

- requires `NO_MMAP` in Linux 6.18;
- only saves cold setup/fd-table state;
- removes the normal fd needed by blind MSG_RING workers.

### Enter wait timeout

- recreated/cancelled on every unrelated wake;
- duplicates the persistent shared-deadline request; and
- makes a long sd-bus deadline more expensive at the TTY module's 16 ms wake
  cadence.

### Multishot timeout

- fixed-rate catch-up is bad when WAMR callbacks consume meaningful time;
- cannot directly produce the linked read's single useful CQE;
- makes cancellation/reconfiguration more stateful.

### Multishot receive and provided buffers

- replace direct guest/bounce ownership with buffer-selection state;
- complicate partial protocol consumption;
- do not match low-rate MPD-sized streams.

### SEND_ZC

- extra notification CQE and request state;
- page pin/account/lifetime cost;
- payloads are too small.

### CQE32, SQE128, mixed CQEs

- no selected operation needs extension space;
- larger ring memory and cache footprint;
- mixed-mode requires stride/skip handling.

### Buffer registration

- no reduction in copy count for the selected sources;
- conflicts with growable WAMR memory;
- exact normal network prep masks reject the advertised fixed-buffer flag.

## 19. Reactor pseudocode

This sketch shows ordering, not every project state:

```c
for (;;) {
    process_ready_blocking_results(); /* cold; before prepare_read */
    enqueue_reload_read_if_requested(); /* never read disk on issuer */
    process_sd_bus_until_idle();

    int dispatched;
    while ((dispatched = wl_display_dispatch_pending(display)) > 0) {}
    if (dispatched < 0)
        goto shutdown;
    while (wl_display_prepare_read(display) != 0) {
        if (wl_display_dispatch_pending(display) < 0)
            goto shutdown;
    }

    const int flush_rc = wl_display_flush(display);
    if (flush_rc < 0 && errno != EAGAIN) {
        wl_display_cancel_read(display);
        goto shutdown;
    }
    reconcile_wayland_oneshot_output(flush_rc < 0); /* only EAGAIN here */
    ensure_wayland_multishot_input(); /* normally already armed */
    reconcile_sd_bus_wait_contract();
    reconcile_shared_deadline_timeout();
    ensure_inotify_read();
    stage_all_pending_request_work();

    uint32_t count;
    long enter_rc;

    for (;;) {
        enter_rc = uring_enter(&ring.hot, 1);
        count = uring_cq_begin(&ring.hot);

        if (count != 0)
            break;
        if (enter_rc < 0)
            break;              /* EINTR/error: resolve Wayland with cancel */

        /*
         * Normal zero-CQE/short-submit path. Keep Wayland prepared and
         * immediately run deferred work / submit leftovers again.
         */
    }

    bool wl_readable = false;
    bool wl_writable = false;
    bool wl_terminal = false;

    for (uint32_t i = 0; i < count; ++i) {
        const struct io_uring_cqe *cqe = uring_cqe_at(&ring.hot, i);
        classify_wayland_cqe(cqe, &wl_readable, &wl_writable, &wl_terminal);
    }

    if (wl_readable) {
        if (wl_display_read_events(display) < 0)
            goto shutdown;
    } else {
        wl_display_cancel_read(display);
        if (wl_terminal)
            goto shutdown;
    }

    if (wl_display_dispatch_pending(display) < 0)
        goto shutdown;
    if (wl_terminal)
        goto shutdown;         /* drain co-reported input first, then stop */
    if (wl_writable) {
        const int post_flush_rc = wl_display_flush(display);
        if (post_flush_rc < 0 && errno != EAGAIN)
            goto shutdown;
        /* EAGAIN makes the next turn rearm the one-shot POLLOUT. */
    }

    /*
     * Handle non-Wayland CQEs in their original order. Wayland poll CQEs
     * were already accounted for and are skipped here. Handlers may stage
     * SQEs but may not call enter.
     */
    for (uint32_t i = 0; i < count; ++i)
        handle_non_wayland_cqe(uring_cqe_at(&ring.hot, i));

    uring_cq_commit(&ring.hot, count);

    if (enter_rc < 0 && enter_rc != -EINTR)
        handle_enter_error(enter_rc);
    if (scene_dirty())
        host_wayland_render();
}
```

Production code must also:

- handle Wayland output poll before/after flush correctly;
- keep the Wayland input watcher marked armed only while its CQE has
  `IORING_CQE_F_MORE`;
- process terminal poll bits;
- handle `-EBADR` as a fatal CQ drop/lost-state condition;
- retain control-operation tags for skipped-success failures;
- avoid running an ordinary CQE twice after the Wayland pre-pass; and
- place sd-bus processing after the Wayland read boundary if callbacks can
  reach libwayland.

## 20. Implementation order

1. **Correct memory ordering first.**
   Add cached SQ/CQ heads, acquire SQ head, and lock-free atomic assertions.
2. **Centralize enter.**
   Remove every handler/reservation-path enter and establish the 256-entry
   staging bound.
3. **Add the normal zero-CQE inner enter loop.**
4. **Split hot/cold ring state and 64-byte-align the 128-byte request pool.**
5. **Read and dispatch Wayland before WAMR callbacks in a CQ batch.**
6. **Implement complete sd-bus events and the shared absolute deadline
   scheduler.**
7. **Make Wayland input multishot; keep output conditional and one-shot.**
   Assert the libwayland drain contract and track `CQE_F_MORE` explicitly.
8. **Remove RTC sysfs from the issuer and close the proc sampler allowlist.**
   Use the vDSO realtime import, `O_NONBLOCK` on exact proc entries, and finish
   their open/first-read warmup before the steady Wayland loop.
9. **Replace inotify poll/read with direct in-flight read.**
10. **Make every fixed-resource teardown cancel->close->retire before reuse.**
    Use two phases while direct-slot installation remains unproven.
11. **Use `SOCK_NONEMPTY` and adaptive receive `POLL_FIRST`.**
12. **Link direct open->read, socket->connect, and known-address
    timeout->socket->connect.**
13. **Replace DNS eventfd with blind MSG_RING and route DNS, spawn, and config
    read/parse through explicit worker lanes with teardown synchronization.**
14. **Use registered-ring indices for later register calls.**
15. **Add separate `MADV_DONTFORK` and `MADV_DONTDUMP` advice calls.**
16. **Make `NO_IOWAIT` an explicit latency/efficiency profile.**
17. **Optionally replace libc `syscall` with audited per-architecture raw enter.**

Each step should compile and pass correctness tests independently. Do not
combine the ordering fix with all state-machine changes in one unreviewable
patch.

## 21. Verification plan without adopting an existing benchmark

### 21.1 Compile-time

- assert SQE/CQE ABI sizes;
- assert 32-bit atomics are always lock-free and representation-compatible;
- assert every hot-field offset and `UringHot` size/alignment;
- assert `sizeof(IORequest)==128`, pool base alignment 64, and request pointer
  tag alignment;
- build x86-64 and AArch64 if both are supported;
- inspect generated enter and `get_sqe` assembly.

### 21.2 Ring contract tests

- force SQ wrap repeatedly and verify no reused SQE is observed stale;
- force CQ wrap and batch retirement;
- inject one invalid SQE amid valid entries and prove `SUBMIT_ALL` progress;
- exercise a nonnegative zero-CQE enter;
- exercise short submit accounting;
- force CQ overflow in a test-only tiny CQ and validate diagnostics;
- close the ring with every operation class in flight.

### 21.3 Operation-state tests

- creator success/failure for open->read, socket->connect, and
  timeout->socket->connect links;
- direct-creator cancellation returning `-EALREADY`, proving close is withheld
  until a later exact/fixed cancellation phase catches the installed slot;
- `RESOLVE_CACHED` miss and controlled fallback;
- a Wayland input burst larger than 4 KiB, proving libwayland grows its input
  ring, drains to `EAGAIN`, and the multishot watcher reports the next edge;
- Wayland `POLLIN` and conditional `POLLOUT` in the same batch;
- sd-bus `POLLIN`, `POLLOUT`, finite timeout, changed timeout, and infinite
  timeout;
- shared-deadline add/update/remove, coincident WAMR+bus expiry, fixed-delay
  callback rearm, and update `-ENOENT`/`-EALREADY` races;
- inotify record burst larger than one logical record;
- datetime ticks use the vDSO clock path and never open RTC sysfs;
- receive CQE with and without `SOCK_NONEMPTY`;
- short send/write and stream teardown while a buffer is kernel-owned;
- reconnect/respawn cannot begin until every old-resource CQE is retired;
- coalesced DNS/spawn/reload results during idle wait and during teardown;
- a stale spawn result closes its real fd instead of installing it; and
- live config/module/font reload performs no issuer-side path lookup, disk
  read, or config parse.

### 21.4 Concurrency/lifetime

- ThreadSanitizer for user-owned DNS queue state where feasible;
- a weak-memory architecture run for the shared-ring acquire/release paths;
- fork while the ring exists and confirm ring mappings are absent in the
  child;
- WAMR `memory.grow` during a growable-buffer request;
- quarantine a module with timer/read/send completions pending;
- ensure detached DNS workers cannot notify a closed/reused ring fd.

### 21.5 Source-level performance validation

These checks validate the design without borrowing anyone else's benchmark:

- count steady-state syscalls per external wake from trace;
- verify no `iou-wrk` task appears;
- trace issuer off-CPU stacks and reject hardware-driver sleep beneath any
  admitted inline read;
- verify no mid-handler `io_uring_enter` call site remains;
- inspect cache-line offsets in debug info;
- trace CQ batch sizes and zero-CQE retries;
- record wake-to-Wayland-read and wake-to-render distributions on the actual
  bar workload if empirical validation is later desired.

The final item is an application latency measurement, not a generic io_uring
benchmark. It validates that the selected scheduling profile behaves as
intended on the deployment machine.

### 21.6 Verification executed on 2026-07-20 and 2026-07-21

The implemented design was validated on Linux 6.18.38, not merely compiled
against the snapshot:

- GCC 15.2 and Clang 21.1 build the complete host without project warnings;
  the GCC release build and the full wired suite also pass with LTO.
- GCC's static analyzer reports no findings in `io_uring_core.c`; the expanded
  ring test passes under ASan+UBSan (including leak detection) and TSan.
- A real AArch64 GNU cross compiler accepts the complete raw-ring test. Its
  disassembly shows `stlr` for SQ-tail publication, syscall number 426 in
  `x8`, `svc 0`, then `ldar` for SQ-head acquisition. x86-64 release
  disassembly likewise shows the hot enter as a direct `syscall` with `r10`
  carrying flags, `r8/r9` zero, and no libc call on that path.
- The live contract suite covers setup query/probe, direct/fallback open,
  successful and failed linked creators, timeout/read and socket/connect,
  send/receive plus `SOCK_NONEMPTY`, one-shot and multishot poll, raced poll
  update ordering, absolute timeout add/update/remove, exact and fixed-file
  cancellation, blind cross-thread MSG_RING, direct inotify read, malformed
  SQE forward progress, 640-entry SQ/CQ wraparound, DONTFORK, deliberate
  NODROP overflow and ordered recovery, and ring close with live recv/poll/
  timeout requests. No `iou-wrk-*` task appears.
- A headless Sway + session-bus integration run exercised rapid proc sampling,
  pure timers, refused-socket reconnects, exec spawning, tray/sd-bus, two
  worker-parsed reloads, and compositor-disconnect teardown. The process had
  exactly the issuer plus three declared workers, no io-wq task, no RTC or
  eventfd, no registered buffers, no pending SQE/CQE at inspection, and only
  the intended `/proc/stat`, `/proc/meminfo`, Wayland, DBus, and inotify fixed
  files. It exited cleanly when the compositor disconnected.
- The final packaged AOT bar was also started in the operator's real Wayland
  session with the real `~/.config/wayring` configuration and MPD filesystem
  socket. MPD completed INIT without restart or quarantine; this exercised the
  semantic effect transaction and Unix socket path that a protocol-shape test
  had previously missed. A simultaneous installed instance owned the tray
  service name, so the verifier's expected duplicate-watcher warning was not
  treated as an MPD or io_uring failure.
- After closing the input-listener P0, the rebuilt store package was traced
  under the operator's niri session while a temporary virtual wheel focused an
  otherwise empty part of the layer surface. The compositor delivered
  `axis_source(0)`, `axis_value120(0, 120)`,
  `axis_relative_direction(0, 0)`, the coupled continuous `axis`, and `frame`
  in order. The parent survived repeated sequences and retained all four
  expected packaged AOT workers; no listener error, restart, or quarantine
  occurred. The verifier and its virtual input device were then removed.

Tracefs was unavailable in the development shell, so kernel tracepoint claims
were checked from the immutable source snapshot and live task/fdinfo state
rather than a trace capture. No third-party or pre-existing benchmark result
was used.

## 22. Primary source map

### Project sources inspected

- raw ring ABI, setup, mappings, registration, prep helpers, and current
  ordering: `host/uring.h`;
- the complete reactor, request pool, every CQ tag/state machine, DNS worker,
  fixed-slot ownership, timer chains, inotify, PTY, and exec pipe paths:
  `host/io_uring_core.c`;
- WAMR memory-growth/pinning and quarantine lifetimes:
  `host/wasm_runtime.c`, `shared/wayring_abi.h`;
- live config reload, disk parsing, module byte acquisition, and hotplug load:
  `host/config.c`, `host/config.h`, `host/wasm_runtime.c`,
  `host/wayland_ui.c`;
- concrete payload sizes and callback cadence:
  `module/module_cpu.c`, `module/module_mem.c`, `module/module_datetime.c`,
  `module/module_mpd.c`, `module/module_exec.c`, `module/module_tty.c`;
- Wayland rendering/input and sd-bus tray callbacks:
  `host/wayland_ui.c`, `host/tray.c`;
- build flags and current raw-ring contract tests:
  `Makefile`, `flake.nix`, `tests/uring_smoke.c`.

### Exact checked-in Linux 6.18.37 sources

The following line anchors make the central claims directly auditable against
the immutable snapshot used here. They are deliberately implementation
anchors, not references to a newer installed header:

| Claim | Snapshot source and line range |
|---|---|
| SQ/CQ acquire-release contract | `io_uring/io_uring.c:6-37` |
| kernel SQ head release and cached SQE consumption | `io_uring/io_uring.c:2399-2448` |
| pending-I/O sleep and `in_iowait` | `io_uring/io_uring.c:2529-2535`, `2642-2653` |
| deferred local work at the CQ wait boundary | `io_uring/io_uring.c:2681-2699`, `2748-2767` |
| registered-ring enter lookup | `io_uring/io_uring.c:3505-3536` |
| setup validation/default CQ/task-complete mode | `io_uring/io_uring.c:3734-3820`, `3869-3875` |
| common SQE/setup flags | `include/uapi/linux/io_uring.h:151-232` |
| enter and feature flags | `include/uapi/linux/io_uring.h:571-619` |
| blind MSG_RING and registered-ring register bit | `include/uapi/linux/io_uring.h:678-696` |
| blind query masks and result ABI | `include/uapi/linux/io_uring/query.h:10-36`; `io_uring/query.c:8-87` |
| poll add/update acceptance | `io_uring/poll.c:832-894` |
| timeout prep/update/expiry | `io_uring/timeout.c:254-275`, `442-538` |
| CQE-skip failure revocation and silent failed links | `io_uring/io_uring.h:300-306`; `io_uring/timeout.c:159-205` |
| recv/send accepted flags and receive state | `io_uring/net.c:418-452`, `784-860` |
| blind MSG_RING registration/remote injection | `io_uring/register.c:904-918`, `io_uring/msg_ring.c:68-114`, `322-344` |
| async-cancel selectors and fixed-file matching | `io_uring/cancel.c:31-67`, `139-233` |
| read fast-poll versus worker decision | `io_uring/rw.c:38-50`, `921-975`; `io_uring/io_uring.c:2075-2097` |
| fixed-file installation and retained request reference | `io_uring/filetable.c:62-123`; `io_uring/io_uring.c:2030-2045` |
| inotify poll/read support and nonblocking construction | `fs/notify/inotify/inotify_user.c:357-366`, `695-717` |
| kernfs always-readable poll and sysfs read dispatch | `fs/kernfs/file.c:239-299`, `855-882`; `fs/sysfs/file.c:220-249` |
| RTC sysfs to sleeping driver callback | `drivers/rtc/sysfs.c:60-72`; `drivers/rtc/interface.c:84-124` |
| schedutil I/O-wait boost and scheduler propagation | `kernel/sched/cpufreq_schedutil.c:238-305`; `kernel/sched/fair.c:7007-7012` |

- UAPI flags, structures, opcodes:
  `resources/linux/include/uapi/linux/io_uring.h`
- memory-ordering contract, SQ consumption, enter, setup validation,
  `task_complete`/lockless CQ, I/O-wait marking:
  `resources/linux/io_uring/io_uring.c`
- internal accepted setup/enter/SQE flag masks:
  `resources/linux/io_uring/io_uring.h`
- poll event parsing, add/update validation, multishot:
  `resources/linux/io_uring/poll.c`
- timeout expiry, link behavior, prep/update:
  `resources/linux/io_uring/timeout.c`
- asynchronous cancellation matching and fixed-fd handling:
  `resources/linux/io_uring/cancel.c`
- ordinary send/recv flags, `POLL_FIRST`, `SOCK_NONEMPTY`, SEND_ZC,
  socket/connect:
  `resources/linux/io_uring/net.c`
- blind `REGISTER_SEND_MSG_RING`:
  `resources/linux/io_uring/register.c`
- blind `REGISTER_QUERY` and returned flag masks:
  `resources/linux/io_uring/query.c`,
  `resources/linux/include/uapi/linux/io_uring/query.h`
- remote/local MSG_RING CQ injection:
  `resources/linux/io_uring/msg_ring.c`
- fixed-slot installation:
  `resources/linux/io_uring/filetable.c`
- read nonblocking/poll/io-wq decision:
  `resources/linux/io_uring/rw.c`
- normal mmap behavior:
  `resources/linux/io_uring/memmap.c`
- inotify read/poll implementation:
  `resources/linux/fs/notify/inotify/inotify_user.c`
- kernfs/sysfs readiness and the hardware-backed RTC callback:
  `resources/linux/fs/kernfs/file.c`, `resources/linux/fs/sysfs/file.c`,
  `resources/linux/drivers/rtc/sysfs.c`,
  `resources/linux/drivers/rtc/interface.c`
- schedutil I/O-wait boost:
  `resources/linux/kernel/sched/cpufreq_schedutil.c`
- scheduler propagation of I/O-wait wake:
  `resources/linux/kernel/sched/fair.c`
- intel_pstate I/O-wait policy:
  `resources/linux/drivers/cpufreq/intel_pstate.c`

Stable source browser entry:
[Linux v6.18.37 io_uring source](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/io_uring?h=v6.18.37)

### External primary documentation

- [Wayland client API and prepare/read/cancel sequence](https://wayland.freedesktop.org/docs/html/apb.html)
- [libwayland 1.25.0 client prepare/read path](https://chromium.googlesource.com/external/anongit.freedesktop.org/git/wayland/wayland/+/refs/tags/upstream/1.25.0/src/wayland-client.c)
- [libwayland 1.25.0 growable connection buffer and drain loop](https://chromium.googlesource.com/external/anongit.freedesktop.org/git/wayland/wayland/+/refs/tags/upstream/1.25.0/src/connection.c)
- [systemd v260.1 sd_bus_get_fd/events/timeout source manual](https://github.com/systemd/systemd/blob/v260.1/man/sd_bus_get_fd.xml)
- [liburing io_uring_setup manual, including `IORING_FEAT_LINKED_FILE`](https://github.com/axboe/liburing/blob/master/man/io_uring_setup.2)
- [Linux 6.18 io_uring zero-copy receive documentation](https://docs.kernel.org/6.18/networking/iou-zcrx.html)

## 23. Final configuration card

```text
Ring count              1
Owner                   Wayland/reactor thread only
SQ                      256
CQ                      512 (kernel default)
SQE/CQE                 64 / 16 bytes
Setup flags             SINGLE_ISSUER | DEFER_TASKRUN |
                        NO_SQARRAY | SUBMIT_ALL
Enter flags, latency    GETEVENTS | REGISTERED_RING
Enter flags, efficiency GETEVENTS | REGISTERED_RING | NO_IOWAIT
min_complete            1
Files                   sparse fixed table; explicit slots
Ring fd                 self-registered for enter/register; real fd retained
Buffers                 ordinary user buffers; no registered/provided buffers
Wayland input           persistent multishot POLLIN; require MORE while live
Wayland output          conditional one-shot poll
sd-bus                  dynamic one-shot poll + shared absolute deadline item
inotify                 one outstanding direct READ
periodic proc sampler   relative TIMEOUT -> READ link, timer CQE skipped
wall clock              shared deadline + vDSO CLOCK_REALTIME host import
pure WAMR timer         shared absolute scheduler; fixed delay after callback
socket receive          ordinary RECV; adaptive POLL_FIRST from CQE flags
socket send             ordinary SEND; one buffer in flight
known-address reconnect TIMEOUT -> SOCKET_DIRECT -> CONNECT link
blocking worker wake    coalesced blind REGISTER_SEND_MSG_RING
blocking worker lanes   DNS, spawn, config read/parse, optional physical RTC
resource teardown       cancel/close/retire; two phases for unproven creator
io-wq                   absent in normal path; any queue_async trace is a
                        degraded resource-invariant violation
mapping                 kernel mmap + DONTFORK + DONTDUMP
CQ handling             acquire snapshot, Wayland pre-pass, ordered handlers,
                        one release head commit
SQ exhaustion           impossible by bound; never enter from a handler
```

That is the project-specific Linux 6.18 state-of-the-art layout.
