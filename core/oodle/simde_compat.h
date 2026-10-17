/* simde_compat.h - map simde's SSE2 wrapper names onto the native
   intrinsics. The ooz source targets simde (a git submodule the source
   tarball does not carry); on x86_64 each has a direct native
   equivalent with the same signature: simde_mm_foo -> _mm_foo.
   Generated, do not edit. */
#pragma once
#include <emmintrin.h>
#include <xmmintrin.h>
#include <mmintrin.h>

#define simde__m128i __m128i
#define simde__m64 __m64
#define simde_mm_add_epi16 _mm_add_epi16
#define simde_mm_add_epi32 _mm_add_epi32
#define simde_mm_add_epi8 _mm_add_epi8
#define simde_mm_and_si128 _mm_and_si128
#define simde_mm_castsi128_ps _mm_castsi128_ps
#define simde_mm_cmpeq_epi16 _mm_cmpeq_epi16
#define simde_mm_cmpeq_epi32 _mm_cmpeq_epi32
#define simde_mm_cmpeq_epi8 _mm_cmpeq_epi8
#define simde_mm_cmpgt_epi16 _mm_cmpgt_epi16
#define simde_mm_cmpgt_epi32 _mm_cmpgt_epi32
#define simde_mm_cvtsi32_si128 _mm_cvtsi32_si128
#define simde_mm_load_si128 _mm_load_si128
#define simde_mm_loadl_epi64 _mm_loadl_epi64
#define simde_mm_loadu_si128 _mm_loadu_si128
#define simde_mm_max_epu8 _mm_max_epu8
#define simde_mm_min_epi16 _mm_min_epi16
#define simde_mm_movemask_epi8 _mm_movemask_epi8
#define simde_mm_or_si128 _mm_or_si128
#define simde_mm_packs_epi16 _mm_packs_epi16
#define simde_mm_packs_epi32 _mm_packs_epi32
#define simde_mm_prefetch _mm_prefetch
#define simde_mm_set1_epi16 _mm_set1_epi16
#define simde_mm_set1_epi32 _mm_set1_epi32
#define simde_mm_set1_epi8 _mm_set1_epi8
#define simde_mm_set_epi16 _mm_set_epi16
#define simde_mm_shuffle_epi32 _mm_shuffle_epi32
#define simde_mm_shufflehi_epi16 _mm_shufflehi_epi16
#define simde_mm_shufflelo_epi16 _mm_shufflelo_epi16
#define simde_mm_slli_si128 _mm_slli_si128
#define simde_mm_srai_epi16 _mm_srai_epi16
#define simde_mm_srli_epi16 _mm_srli_epi16
#define simde_mm_store_si128 _mm_store_si128
#define simde_mm_storeh_pi _mm_storeh_pi
#define simde_mm_storel_epi64 _mm_storel_epi64
#define simde_mm_storeu_si128 _mm_storeu_si128
#define simde_mm_sub_epi16 _mm_sub_epi16
#define simde_mm_sub_epi32 _mm_sub_epi32
#define simde_mm_sub_epi8 _mm_sub_epi8
#define simde_mm_unpackhi_epi8 _mm_unpackhi_epi8
#define simde_mm_unpacklo_epi16 _mm_unpacklo_epi16
#define simde_mm_unpacklo_epi8 _mm_unpacklo_epi8
#define simde_mm_xor_si128 _mm_xor_si128

/* uppercase simde constants: SIMDE_MM_FOO -> _MM_FOO */
#define SIMDE_MM_HINT_T0 _MM_HINT_T0
