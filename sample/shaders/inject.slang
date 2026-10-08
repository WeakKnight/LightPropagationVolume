#include "common.slang"
#include "scene.slang"
#include "lpv.slang"

struct SurfaceSample
{
    float4 position_area;
    float4 normal;
    float4 albedo;
    float4 emission;
};

uniform Grid g_grid;
uniform bool g_point_projection;
uniform Scene g_scene;
uniform Sun g_sun;
StructuredBuffer<SurfaceSample> g_samples;
StructuredBuffer<uint2> g_ranges;
RWStructuredBuffer<SHCell> g_source;

[shader("compute")]
[numthreads(4, 4, 4)]
void compute_main(uint3 tid : SV_DispatchThreadID)
{
    if (any(tid >= g_grid.resolution)) return;
    uint index = g_grid.index(int3(tid));
    uint2 range = g_ranges[index];
    SHCell result = {};
    for (uint i = 0; i < range.y; ++i)
    {
        SurfaceSample sample = g_samples[range.x + i];
        Hit hit = {};
        hit.position = sample.position_area.xyz;
        hit.geometric_normal = hit.normal = sample.normal.xyz;
        // Reflected plus emitted flux; a Lambertian Le emits PI*Le per unit area.
        float3 flux = (sample.albedo.xyz * direct_irradiance(g_scene, hit, g_sun)
            + PI*sample.emission.xyz) * sample.position_area.w;
        // Store intensity / cell-face area as the LPV radiance approximation.
        float3 value = flux * INV_PI / (g_grid.cell_size * g_grid.cell_size);
        result = sh_add(result, g_point_projection ?
            sh_from_direction(flux / (g_grid.cell_size*g_grid.cell_size),hit.normal) :
            sh_from_lobe(value, hit.normal));
    }
    g_source[index] = result;
}
