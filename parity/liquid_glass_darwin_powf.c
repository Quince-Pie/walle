#include "liquid_glass_darwin_powf.h"

#include <float.h>
#include <math.h>
#include <stdint.h>
#include <string.h>

static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24);
static_assert(sizeof(double) == 8 && DBL_MANT_DIG == 53);
static_assert(FLT_EVAL_METHOD == 0);

struct log_entry {
    uint64_t reciprocal_bits;
    uint64_t logarithm_bits;
};

static const uint64_t log_coefficients[] = {
    UINT64_C(0xc0471559dca827eb),
    UINT64_C(0x404ec71c53b3b1e8),
    UINT64_C(0xc05715476529654c),
    UINT64_C(0x40671547652aef42),
};

static const struct log_entry log_table[] = {
    { UINT64_C(0x3ff6c16c16c16c17), UINT64_C(0xc05042bd4b9a7c99) },
    { UINT64_C(0x3ff6a13cd1537290), UINT64_C(0xc050014332be0033) },
    { UINT64_C(0x3ff6816816816817), UINT64_C(0xc04f804ae8d0cd02) },
    { UINT64_C(0x3ff661ec6a5122f9), UINT64_C(0xc04efec61b011f85) },
    { UINT64_C(0x3ff642c8590b2164), UINT64_C(0xc04e7df5fe538ab3) },
    { UINT64_C(0x3ff623fa77016240), UINT64_C(0xc04dfdd89d586e2b) },
    { UINT64_C(0x3ff6058160581606), UINT64_C(0xc04d7e6c0abc3579) },
    { UINT64_C(0x3ff5e75bb8d015e7), UINT64_C(0xc04cffae611ad12b) },
    { UINT64_C(0x3ff5c9882b931057), UINT64_C(0xc04c819dc2d45fe4) },
    { UINT64_C(0x3ff5ac056b015ac0), UINT64_C(0xc04c043859e2fdb3) },
    { UINT64_C(0x3ff58ed2308158ed), UINT64_C(0xc04b877c57b1b070) },
    { UINT64_C(0x3ff571ed3c506b3a), UINT64_C(0xc04b0b67f4f46810) },
    { UINT64_C(0x3ff5555555555555), UINT64_C(0xc04a8ff971810a5e) },
    { UINT64_C(0x3ff5390948f40feb), UINT64_C(0xc04a152f142981b4) },
    { UINT64_C(0x3ff51d07eae2f815), UINT64_C(0xc0499b072a96c6b2) },
    { UINT64_C(0x3ff5015015015015), UINT64_C(0xc04921800924dd3b) },
    { UINT64_C(0x3ff4e5e0a72f0539), UINT64_C(0xc048a8980abfbd32) },
    { UINT64_C(0x3ff4cab88725af6e), UINT64_C(0xc048304d90c11fd3) },
    { UINT64_C(0x3ff4afd6a052bf5b), UINT64_C(0xc047b89f02cf2aad) },
    { UINT64_C(0x3ff49539e3b2d067), UINT64_C(0xc047418acebbf18f) },
    { UINT64_C(0x3ff47ae147ae147b), UINT64_C(0xc046cb0f6865c8ea) },
    { UINT64_C(0x3ff460cbc7f5cf9a), UINT64_C(0xc046552b49986277) },
    { UINT64_C(0x3ff446f86562d9fb), UINT64_C(0xc045dfdcf1eeae0e) },
    { UINT64_C(0x3ff42d6625d51f87), UINT64_C(0xc0456b22e6b578e5) },
    { UINT64_C(0x3ff4141414141414), UINT64_C(0xc044f6fbb2cec598) },
    { UINT64_C(0x3ff3fb013fb013fb), UINT64_C(0xc0448365e695d797) },
    { UINT64_C(0x3ff3e22cbce4a902), UINT64_C(0xc044106017c3eca3) },
    { UINT64_C(0x3ff3c995a47babe7), UINT64_C(0xc0439de8e1559f6f) },
    { UINT64_C(0x3ff3b13b13b13b14), UINT64_C(0xc0432bfee370ee68) },
    { UINT64_C(0x3ff3991c2c187f63), UINT64_C(0xc042baa0c34be1ec) },
    { UINT64_C(0x3ff3813813813814), UINT64_C(0xc04249cd2b13cd6c) },
    { UINT64_C(0x3ff3698df3de0748), UINT64_C(0xc041d982c9d52708) },
    { UINT64_C(0x3ff3521cfb2b78c1), UINT64_C(0xc04169c05363f158) },
    { UINT64_C(0x3ff33ae45b57bcb2), UINT64_C(0xc040fa848044b351) },
    { UINT64_C(0x3ff323e34a2b10bf), UINT64_C(0xc0408bce0d95fa38) },
    { UINT64_C(0x3ff30d190130d190), UINT64_C(0xc0401d9bbcfa61d4) },
    { UINT64_C(0x3ff2f684bda12f68), UINT64_C(0xc03f5fd8a9063e35) },
    { UINT64_C(0x3ff2e025c04b8097), UINT64_C(0xc03e857d3d361368) },
    { UINT64_C(0x3ff2c9fb4d812ca0), UINT64_C(0xc03dac22d3e441d3) },
    { UINT64_C(0x3ff2b404ad012b40), UINT64_C(0xc03cd3c712d31109) },
    { UINT64_C(0x3ff29e4129e4129e), UINT64_C(0xc03bfc67a7fff4cc) },
    { UINT64_C(0x3ff288b01288b013), UINT64_C(0xc03b2602497d5346) },
    { UINT64_C(0x3ff27350b8812735), UINT64_C(0xc03a5094b54d2828) },
    { UINT64_C(0x3ff25e22708092f1), UINT64_C(0xc0397c1cb13c7ec1) },
    { UINT64_C(0x3ff2492492492492), UINT64_C(0xc038a8980abfbd32) },
    { UINT64_C(0x3ff23456789abcdf), UINT64_C(0xc037d60496cfbb4c) },
    { UINT64_C(0x3ff21fb78121fb78), UINT64_C(0xc037046031c79f85) },
    { UINT64_C(0x3ff20b470c67c0d9), UINT64_C(0xc03633a8bf437ce1) },
    { UINT64_C(0x3ff1f7047dc11f70), UINT64_C(0xc03563dc29ffacb2) },
    { UINT64_C(0x3ff1e2ef3b3fb874), UINT64_C(0xc03494f863b8df35) },
    { UINT64_C(0x3ff1cf06ada2811d), UINT64_C(0xc033c6fb650cde51) },
    { UINT64_C(0x3ff1bb4a4046ed29), UINT64_C(0xc032f9e32d5bfdd1) },
    { UINT64_C(0x3ff1a7b9611a7b96), UINT64_C(0xc0322dadc2ab3497) },
    { UINT64_C(0x3ff19453808ca29c), UINT64_C(0xc03162593186da70) },
    { UINT64_C(0x3ff1811811811812), UINT64_C(0xc03097e38ce60649) },
    { UINT64_C(0x3ff16e0689427379), UINT64_C(0xc02f9c95dc1d1165) },
    { UINT64_C(0x3ff15b1e5f75270d), UINT64_C(0xc02e0b1ae8f2fd56) },
    { UINT64_C(0x3ff1485f0e0acd3b), UINT64_C(0xc02c7b528b70f1c5) },
    { UINT64_C(0x3ff135c81135c811), UINT64_C(0xc02aed391ab6674e) },
    { UINT64_C(0x3ff12358e75d3033), UINT64_C(0xc02960caf9abb7ca) },
    { UINT64_C(0x3ff1111111111111), UINT64_C(0xc027d60496cfbb4c) },
    { UINT64_C(0x3ff0fef010fef011), UINT64_C(0xc0264ce26c067157) },
    { UINT64_C(0x3ff0ecf56be69c90), UINT64_C(0xc024c560fe68af88) },
    { UINT64_C(0x3ff0db20a88f4696), UINT64_C(0xc0233f7cde14cf5a) },
    { UINT64_C(0x3ff0c9714fbcda3b), UINT64_C(0xc021bb32a600549d) },
    { UINT64_C(0x3ff0b7e6ec259dc8), UINT64_C(0xc020387efbca869e) },
    { UINT64_C(0x3ff0a6810a6810a7), UINT64_C(0xc01d6ebd1f1febfe) },
    { UINT64_C(0x3ff0953f39010954), UINT64_C(0xc01a6f9c377dd31b) },
    { UINT64_C(0x3ff0842108421084), UINT64_C(0xc0177394c9d958d5) },
    { UINT64_C(0x3ff073260a47f7c6), UINT64_C(0xc0147aa07357704f) },
    { UINT64_C(0x3ff0624dd2f1a9fc), UINT64_C(0xc01184b8e4c56af8) },
    { UINT64_C(0x3ff05197f7d73404), UINT64_C(0xc00d23afc49139f9) },
    { UINT64_C(0x3ff0410410410410), UINT64_C(0xc00743ee861f3556) },
    { UINT64_C(0x3ff03091b51f5e1a), UINT64_C(0xc0016a21e20a0a45) },
    { UINT64_C(0x3ff0204081020408), UINT64_C(0xbff72c7ba20f7327) },
    { UINT64_C(0x3ff0101010101010), UINT64_C(0xbfe720d9c06a835f) },
    { UINT64_C(0x3ff0000000000000), UINT64_C(0x0000000000000000) },
    { UINT64_C(0x3fefc07f01fc07f0), UINT64_C(0x3ff6fe50b6ef0851) },
    { UINT64_C(0x3fef81f81f81f820), UINT64_C(0x4006e79685c2d22a) },
    { UINT64_C(0x3fef44659e4a4271), UINT64_C(0x40111cd1d5133413) },
    { UINT64_C(0x3fef07c1f07c1f08), UINT64_C(0x4016bad3758efd87) },
    { UINT64_C(0x3feecc07b301ecc0), UINT64_C(0x401c4dfab90aab5f) },
    { UINT64_C(0x3fee9131abf0b767), UINT64_C(0x4020eb389fa29f9b) },
    { UINT64_C(0x3fee573ac901e574), UINT64_C(0x4023aa2fdd27f1c3) },
    { UINT64_C(0x3fee1e1e1e1e1e1e), UINT64_C(0x402663f6fac91316) },
    { UINT64_C(0x3fede5d6e3f8868a), UINT64_C(0x402918a16e46335b) },
    { UINT64_C(0x3fedae6076b981db), UINT64_C(0x402bc84240adabba) },
    { UINT64_C(0x3fed77b654b82c34), UINT64_C(0x402e72ec117fa5b2) },
    { UINT64_C(0x3fed41d41d41d41d), UINT64_C(0x40308c588cda79e4) },
    { UINT64_C(0x3fed0cb58f6ec074), UINT64_C(0x4031dcd197552b7b) },
    { UINT64_C(0x3fecd85689039b0b), UINT64_C(0x40332ae9e278ae1a) },
    { UINT64_C(0x3feca4b3055ee191), UINT64_C(0x403476a9f983f74d) },
    { UINT64_C(0x3fec71c71c71c71c), UINT64_C(0x4035c01a39fbd688) },
    { UINT64_C(0x3fec3f8f01c3f8f0), UINT64_C(0x40370742d4ef027f) },
    { UINT64_C(0x3fec0e070381c0e0), UINT64_C(0x40384c2bd02f03b3) },
    { UINT64_C(0x3febdd2b899406f7), UINT64_C(0x40398edd077e70df) },
    { UINT64_C(0x3febacf914c1bad0), UINT64_C(0x403acf5e2db4ec94) },
    { UINT64_C(0x3feb7d6c3dda338b), UINT64_C(0x403c0db6cdd94dee) },
    { UINT64_C(0x3feb4e81b4e81b4f), UINT64_C(0x403d49ee4c325970) },
    { UINT64_C(0x3feb2036406c80d9), UINT64_C(0x403e840be74e6a4d) },
    { UINT64_C(0x3feaf286bca1af28), UINT64_C(0x403fbc16b902680a) },
    { UINT64_C(0x3feac5701ac5701b), UINT64_C(0x4040790adbb03009) },
    { UINT64_C(0x3fea98ef606a63be), UINT64_C(0x40411307dad30b76) },
    { UINT64_C(0x3fea6d01a6d01a6d), UINT64_C(0x4041ac05b291f070) },
    { UINT64_C(0x3fea41a41a41a41a), UINT64_C(0x40424407ab0e073a) },
    { UINT64_C(0x3fea16d3f97a4b02), UINT64_C(0x4042db10fc4d9aaf) },
    { UINT64_C(0x3fe9ec8e951033d9), UINT64_C(0x40437124cea4cded) },
    { UINT64_C(0x3fe9c2d14ee4a102), UINT64_C(0x404406463b1b0449) },
    { UINT64_C(0x3fe999999999999a), UINT64_C(0x40449a784bcd1b8b) },
    { UINT64_C(0x3fe970e4f80cb872), UINT64_C(0x40452dbdfc4c96b3) },
    { UINT64_C(0x3fe948b0fcd6e9e0), UINT64_C(0x4045c01a39fbd688) },
    { UINT64_C(0x3fe920fb49d0e229), UINT64_C(0x4046518fe4677ba7) },
    { UINT64_C(0x3fe8f9c18f9c18fa), UINT64_C(0x4046e221cd9d0cde) },
    { UINT64_C(0x3fe8d3018d3018d3), UINT64_C(0x404771d2ba7efb3c) },
    { UINT64_C(0x3fe8acb90f6bf3aa), UINT64_C(0x404800a563161c54) },
    { UINT64_C(0x3fe886e5f0abb04a), UINT64_C(0x40488e9c72e0b226) },
    { UINT64_C(0x3fe8618618618618), UINT64_C(0x40491bba891f1709) },
    { UINT64_C(0x3fe83c977ab2bedd), UINT64_C(0x4049a802391e232f) },
    { UINT64_C(0x3fe8181818181818), UINT64_C(0x404a33760a7f6051) },
    { UINT64_C(0x3fe7f405fd017f40), UINT64_C(0x404abe18797f1f49) },
    { UINT64_C(0x3fe7d05f417d05f4), UINT64_C(0x404b47ebf73882a1) },
    { UINT64_C(0x3fe7ad2208e0ecc3), UINT64_C(0x404bd0f2e9e79031) },
    { UINT64_C(0x3fe78a4c8178a4c8), UINT64_C(0x404c592fad295b56) },
    { UINT64_C(0x3fe767dce434a9b1), UINT64_C(0x404ce0a4923a587d) },
    { UINT64_C(0x3fe745d1745d1746), UINT64_C(0x404d6753e032ea0f) },
    { UINT64_C(0x3fe724287f46debc), UINT64_C(0x404ded3fd442364c) },
    { UINT64_C(0x3fe702e05c0b8170), UINT64_C(0x404e726aa1e754d2) },
    { UINT64_C(0x3fe6e1f76b4337c7), UINT64_C(0x404ef6d67328e220) },
};

static const uint64_t exp_coefficients[] = {
    UINT64_C(0x3eeebfbdff30d656),
    UINT64_C(0x3f762e4453e10dae),
};

static const uint64_t exp_table[] = {
    UINT64_C(0x3ff0000000000000),
    UINT64_C(0x3feff63da9fb3335),
    UINT64_C(0x3fefec9a3e778061),
    UINT64_C(0x3fefe315e86e7f85),
    UINT64_C(0x3fefd9b0d3158574),
    UINT64_C(0x3fefd06b29ddf6de),
    UINT64_C(0x3fefc74518759bc8),
    UINT64_C(0x3fefbe3ecac6f383),
    UINT64_C(0x3fefb5586cf9890f),
    UINT64_C(0x3fefac922b7247f7),
    UINT64_C(0x3fefa3ec32d3d1a2),
    UINT64_C(0x3fef9b66affed31b),
    UINT64_C(0x3fef9301d0125b51),
    UINT64_C(0x3fef8abdc06c31cc),
    UINT64_C(0x3fef829aaea92de0),
    UINT64_C(0x3fef7a98c8a58e51),
    UINT64_C(0x3fef72b83c7d517b),
    UINT64_C(0x3fef6af9388c8dea),
    UINT64_C(0x3fef635beb6fcb75),
    UINT64_C(0x3fef5be084045cd4),
    UINT64_C(0x3fef54873168b9aa),
    UINT64_C(0x3fef4d5022fcd91d),
    UINT64_C(0x3fef463b88628cd6),
    UINT64_C(0x3fef3f49917ddc96),
    UINT64_C(0x3fef387a6e756238),
    UINT64_C(0x3fef31ce4fb2a63f),
    UINT64_C(0x3fef2b4565e27cdd),
    UINT64_C(0x3fef24dfe1f56381),
    UINT64_C(0x3fef1e9df51fdee1),
    UINT64_C(0x3fef187fd0dad990),
    UINT64_C(0x3fef1285a6e4030b),
    UINT64_C(0x3fef0cafa93e2f56),
    UINT64_C(0x3fef06fe0a31b715),
    UINT64_C(0x3fef0170fc4cd831),
    UINT64_C(0x3feefc08b26416ff),
    UINT64_C(0x3feef6c55f929ff1),
    UINT64_C(0x3feef1a7373aa9cb),
    UINT64_C(0x3feeecae6d05d866),
    UINT64_C(0x3feee7db34e59ff7),
    UINT64_C(0x3feee32dc313a8e5),
    UINT64_C(0x3feedea64c123422),
    UINT64_C(0x3feeda4504ac801c),
    UINT64_C(0x3feed60a21f72e2a),
    UINT64_C(0x3feed1f5d950a897),
    UINT64_C(0x3feece086061892d),
    UINT64_C(0x3feeca41ed1d0057),
    UINT64_C(0x3feec6a2b5c13cd0),
    UINT64_C(0x3feec32af0d7d3de),
    UINT64_C(0x3feebfdad5362a27),
    UINT64_C(0x3feebcb299fddd0d),
    UINT64_C(0x3feeb9b2769d2ca7),
    UINT64_C(0x3feeb6daa2cf6642),
    UINT64_C(0x3feeb42b569d4f82),
    UINT64_C(0x3feeb1a4ca5d920f),
    UINT64_C(0x3feeaf4736b527da),
    UINT64_C(0x3feead12d497c7fd),
    UINT64_C(0x3feeab07dd485429),
    UINT64_C(0x3feea9268a5946b7),
    UINT64_C(0x3feea76f15ad2148),
    UINT64_C(0x3feea5e1b976dc09),
    UINT64_C(0x3feea47eb03a5585),
    UINT64_C(0x3feea34634ccc320),
    UINT64_C(0x3feea23882552225),
    UINT64_C(0x3feea155d44ca973),
    UINT64_C(0x3feea09e667f3bcd),
    UINT64_C(0x3feea012750bdabf),
    UINT64_C(0x3fee9fb23c651a2f),
    UINT64_C(0x3fee9f7df9519484),
    UINT64_C(0x3fee9f75e8ec5f74),
    UINT64_C(0x3fee9f9a48a58174),
    UINT64_C(0x3fee9feb564267c9),
    UINT64_C(0x3feea0694fde5d3f),
    UINT64_C(0x3feea11473eb0187),
    UINT64_C(0x3feea1ed0130c132),
    UINT64_C(0x3feea2f336cf4e62),
    UINT64_C(0x3feea427543e1a12),
    UINT64_C(0x3feea589994cce13),
    UINT64_C(0x3feea71a4623c7ad),
    UINT64_C(0x3feea8d99b4492ed),
    UINT64_C(0x3feeaac7d98a6699),
    UINT64_C(0x3feeace5422aa0db),
    UINT64_C(0x3feeaf3216b5448c),
    UINT64_C(0x3feeb1ae99157736),
    UINT64_C(0x3feeb45b0b91ffc6),
    UINT64_C(0x3feeb737b0cdc5e5),
    UINT64_C(0x3feeba44cbc8520f),
    UINT64_C(0x3feebd829fde4e50),
    UINT64_C(0x3feec0f170ca07ba),
    UINT64_C(0x3feec49182a3f090),
    UINT64_C(0x3feec86319e32323),
    UINT64_C(0x3feecc667b5de565),
    UINT64_C(0x3feed09bec4a2d33),
    UINT64_C(0x3feed503b23e255d),
    UINT64_C(0x3feed99e1330b358),
    UINT64_C(0x3feede6b5579fdbf),
    UINT64_C(0x3feee36bbfd3f37a),
    UINT64_C(0x3feee89f995ad3ad),
    UINT64_C(0x3feeee07298db666),
    UINT64_C(0x3feef3a2b84f15fb),
    UINT64_C(0x3feef9728de5593a),
    UINT64_C(0x3feeff76f2fb5e47),
    UINT64_C(0x3fef05b030a1064a),
    UINT64_C(0x3fef0c1e904bc1d2),
    UINT64_C(0x3fef12c25bd71e09),
    UINT64_C(0x3fef199bdd85529c),
    UINT64_C(0x3fef20ab5fffd07a),
    UINT64_C(0x3fef27f12e57d14b),
    UINT64_C(0x3fef2f6d9406e7b5),
    UINT64_C(0x3fef3720dcef9069),
    UINT64_C(0x3fef3f0b555dc3fa),
    UINT64_C(0x3fef472d4a07897c),
    UINT64_C(0x3fef4f87080d89f2),
    UINT64_C(0x3fef5818dcfba487),
    UINT64_C(0x3fef60e316c98398),
    UINT64_C(0x3fef69e603db3285),
    UINT64_C(0x3fef7321f301b460),
    UINT64_C(0x3fef7c97337b9b5f),
    UINT64_C(0x3fef864614f5a129),
    UINT64_C(0x3fef902ee78b3ff6),
    UINT64_C(0x3fef9a51fbc74c83),
    UINT64_C(0x3fefa4afa2a490da),
    UINT64_C(0x3fefaf482d8e67f1),
    UINT64_C(0x3fefba1bee615a27),
    UINT64_C(0x3fefc52b376bba97),
    UINT64_C(0x3fefd0765b6e4540),
    UINT64_C(0x3fefdbfdad9cbe14),
    UINT64_C(0x3fefe7c1819e90d8),
    UINT64_C(0x3feff3c22b8f71f1),
};

static double double_from_bits(uint64_t bits)
{
    double value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static float float_from_bits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static uint32_t float_bits(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

bool walle_lg_darwin_powf_positive_normal(
    float base,
    float exponent_value,
    float *result
)
{
    if (result == nullptr || !isfinite(exponent_value)) {
        return false;
    }

    uint32_t base_bits = float_bits(base);
    if (base_bits - UINT32_C(0x00800000) >= UINT32_C(0x7f000000)) {
        return false;
    }

    uint32_t reduction = base_bits - UINT32_C(0x3f338000);
    uint32_t table_index = reduction >> 16 & UINT32_C(0x7f);
    uint32_t exponent_bits = reduction & UINT32_C(0xff800000);
    uint32_t mantissa_bits = base_bits - exponent_bits;

    int64_t signed_exponent_bits = exponent_bits < UINT32_C(0x80000000)
        ? (int64_t)exponent_bits
        : (int64_t)exponent_bits - INT64_C(0x100000000);
    int64_t exponent = signed_exponent_bits / INT64_C(0x10000);

    const struct log_entry *entry = &log_table[table_index];
    double reciprocal = double_from_bits(entry->reciprocal_bits);
    double logarithm = double_from_bits(entry->logarithm_bits);
    double mantissa = (double)float_from_bits(mantissa_bits);
    double remainder = fma(mantissa, reciprocal, -1.0);
    double exponent_logarithm = logarithm + (double)exponent;
    double squared_remainder = remainder * remainder;

    double log_term_0 = fma(
        remainder,
        double_from_bits(log_coefficients[0]),
        double_from_bits(log_coefficients[1])
    );
    double log_term_1 = fma(
        remainder,
        double_from_bits(log_coefficients[2]),
        double_from_bits(log_coefficients[3])
    );
    double logarithm_polynomial = fma(
        log_term_0, squared_remainder, log_term_1
    );
    double reduced_logarithm = fma(
        logarithm_polynomial, remainder, exponent_logarithm
    );

    double exponent_product = (double)exponent_value * reduced_logarithm;
    exponent_product = fmin(exponent_product, 32768.0);
    exponent_product = fmax(exponent_product, -32768.0);

    double rounded_exponent = round(exponent_product);
    int64_t integer_exponent = (int64_t)rounded_exponent;
    double exp_remainder = exponent_product - rounded_exponent;
    uint64_t exp_index = (uint64_t)integer_exponent & UINT64_C(0x7f);
    uint64_t scale_bits = exp_table[exp_index]
        + (uint64_t)integer_exponent * (UINT64_C(1) << 45);
    double scale = double_from_bits(scale_bits);

    double exp_polynomial = fma(
        double_from_bits(exp_coefficients[0]),
        exp_remainder,
        double_from_bits(exp_coefficients[1])
    );
    double scaled_remainder = exp_polynomial * exp_remainder;
    double decoded = fma(scaled_remainder, scale, scale);
    volatile float rounded = (float)decoded;
    *result = rounded;
    return true;
}

bool walle_lg_darwin_powf_2_4(float base, float *result)
{
    return walle_lg_darwin_powf_positive_normal(
        base,
        float_from_bits(UINT32_C(0x4019999a)),
        result
    );
}

bool walle_lg_darwin_powf_1_over_2_2(float base, float* result)
{
    return walle_lg_darwin_powf_positive_normal(
        base, float_from_bits(UINT32_C(0x3ee8ba2e)), result);
}
