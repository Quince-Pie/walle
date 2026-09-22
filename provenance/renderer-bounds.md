# Renderer v4 graphics bounds

This candidate is separate from frozen v3 and its initial benchmark results.
It changes dimension guards only; shader modules, frame packets, draw commands
and synchronization are unchanged for valid extents.

Graphics images and the full-attachment render/viewport path now require:

- Nonzero dimensions within maxImageDimension2D.
- Each axis within maxFramebufferWidth/Height.
- The actual float viewport width and absolute height within maxViewportDimensions.
- For x=0, y=height, width=width, height=-height, both coordinate endpoints lie
  within viewportBoundsRange. NaN ranges are rejected; unbounded ranges are
  limited by the finite image/framebuffer/viewport-dimension caps.

The scalar public max-image API preserves its original image-ceiling semantics,
including UINT32_MAX before device selection. It is not complete graphics
admission. The app uses it for its image/arithmetic ceiling; exact per-axis
backend checks accept valid asymmetric rectangles and reject unsupported
attachments before allocation. No artificial square constraint is introduced.

Guards cover output create/resize, dma-buf presentation images, capture and
color/input/depth/transient attachment image creation. Invalid resize/capture
plans are rejected before waiting for submitted work or discarding cached
resources. Storage/sampled-only images and compute pyramids retain their
separate maxImageDimension2D limit; an image above the framebuffer limit is not
rejected merely because it is a compute texture.

Installed Vulkan1.4.341 validusage.json rules supporting this change:
VkImageCreateInfo-usage-00964/00965; VkRenderingInfo-pNext-07815/07816;
VkViewport-width-01770/01771, height-01773, x-01774/01232 and
 y-01775/01776/01777/01233.

The read-only physical query found maxImageDimension2D32768 on RX9070XT and16384
on the integrated AMD, but both framebuffer dimensions are16384. The v3 image
limit alone was therefore insufficient for graphics attachments on the dGPU.

CPU-only controls passed40 boundary/entrypoint cases. They use synthetic limits,
check inclusive/adjacent limits, asymmetric caps, fractional coordinate bounds,
NaN and unbounded ranges. Invalid graphics allocation/resize/capture paths made
zero image creation/destruction/fence-wait calls. Legal storage dimensions above
the graphics cap reached an injected image-create result. No Vulkan instance,
logical device or GPU work is created by the control. Strict C23 warnings pass.
Root-coordinated GPU regression subsequently passed on all three devices: actual
over-limit rejection preserved allocation counts/peaks, the discrete GPU created
a16385×1 storage-only image beyond its framebuffer limit, and72 optical frames
per device retained exact endpoints. Validation was active with zero checked
teardown errors. See MANIFEST.json for records.
