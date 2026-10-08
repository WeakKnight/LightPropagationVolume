#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
#include "probe_visibility.slang"

uniform Scene g_scene;
uniform Camera g_camera;
uniform Sun g_sun;
uniform Grid g_grid;
uniform uint g_mode;
uniform uint g_sample_base;
uniform uint g_samples_per_frame;
uniform uint g_seed;
uniform uint g_bounces;
uniform bool g_visibility_filter;
uniform float g_read_bias;
uniform float g_indirect_strength;
uniform float g_slice;
StructuredBuffer<SHCell> g_volume;
StructuredBuffer<SHCell> g_source;
#ifdef DIRECTIONAL_READ
uniform uint g_direction_count;
StructuredBuffer<float4> g_directions;
StructuredBuffer<float4> g_angular_total;
StructuredBuffer<float4> g_probe_irradiance;
#endif
RWTexture2D<float4> g_output;

SHCell load_cell(int3 p)
{
    if (!g_grid.contains(p)) return (SHCell)0;
    return g_volume[g_grid.index(p)];
}

SHCell sample_volume(float3 position, Hit hit)
{
    float3 cell = (position - g_grid.origin) / g_grid.cell_size - .5;
    int3 base = int3(floor(cell));
    float3 f = frac(cell);
    SHCell result = {};
    float weight_sum=0;
    for (int z = 0; z < 2; ++z)
    for (int y = 0; y < 2; ++y)
    for (int x = 0; x < 2; ++x)
    {
        float w = (x != 0 ? f.x : 1-f.x) * (y != 0 ? f.y : 1-f.y) * (z != 0 ? f.z : 1-f.z);
        int3 p=base+int3(x,y,z);
        if(!g_grid.contains(p))continue;
        w*=probe_visibility_weight(g_grid,g_scene,p,hit);
        result = sh_add(result, sh_scale(load_cell(p), w));
        weight_sum+=w;
    }
    return g_probe_visibility!=2 && weight_sum>0 ? sh_scale(result,1/weight_sum) : result;
}

float3 lpv_irradiance(Hit hit)
{
    float3 position = hit.position + hit.geometric_normal * (g_read_bias * g_grid.cell_size);
#ifdef DIRECTIONAL_READ
    float3 cell=(position-g_grid.origin)/g_grid.cell_size-.5;
    int3 base=int3(floor(cell));float3 f=frac(cell);
    float3 result=0;float weight_sum=0;
    for(int z=0;z<2;++z)for(int y=0;y<2;++y)for(int x=0;x<2;++x)
    {
        int3 p=base+int3(x,y,z);if(!g_grid.contains(p))continue;
        float w=(x!=0?f.x:1-f.x)*(y!=0?f.y:1-f.y)*(z!=0?f.z:1-f.z);
        w*=probe_visibility_weight(g_grid,g_scene,p,hit);
        if(w<=0)continue;
        float3 irradiance=0;
        if(g_probe_visibility==0)
            irradiance=sample_probe_irradiance(g_probe_irradiance,g_grid.index(p),hit.normal);
        else
        {
            float cosine_sum=0;
            for(uint direction=0;direction<g_direction_count;++direction)
            {
                float4 d=g_directions[direction];float cosine=max(dot(-d.xyz,hit.normal),0)*d.w;
                irradiance+=g_angular_total[g_grid.index(p)*g_direction_count+direction].xyz*cosine;
                cosine_sum+=cosine;
            }
            irradiance*=PI/cosine_sum;
        }
        result+=w*irradiance;weight_sum+=w;
    }
    return weight_sum>0 ? result/weight_sum : float3(0);
#else
    SHCell cell = sample_volume(position,hit);
    // Stored directions follow light travel; incoming light enters the hemisphere
    // centered on -normal. Cosine convolution of SH yields diffuse irradiance.
    return sh_irradiance(cell, -hit.normal);
#endif
}

float3 shade(float3 direction, inout RNG rng)
{
    Hit hit = g_scene.intersect(g_camera.position, direction);
    if (!hit.valid) return float3(.018, .022, .028);
    float3 direct = hit.albedo * INV_PI * direct_irradiance(g_scene, hit, g_sun);
    if (g_mode == 1) return hit.emission + direct;
    float3 indirect;
    if (g_mode == 3)
    {
        // Independent bounded-depth diffuse path reference. At every hit the
        // cosine PDF cancels cos/PI; a material's rho enters throughput once.
        indirect=0;
        float3 throughput=hit.albedo;
        Hit current=hit;
        for (uint bounce=0; bounce<g_bounces; ++bounce)
        {
            float3 direction=cosine_sample(rng.next2(),current.normal);
            Hit secondary=g_scene.intersect(g_scene.offset_origin(current),direction);
            if (!secondary.valid) break;
            indirect += throughput*(secondary.emission+secondary.albedo*INV_PI*
                direct_irradiance(g_scene,secondary,g_sun));
            throughput *= secondary.albedo;
            current=secondary;
            if (max(throughput.x,max(throughput.y,throughput.z))==0) break;
        }
    }
    else indirect = hit.albedo * INV_PI * lpv_irradiance(hit);
    indirect *= g_indirect_strength;
    return g_mode == 2 ? indirect : hit.emission + direct + indirect;
}

[shader("compute")]
[numthreads(8, 8, 1)]
void compute_main(uint3 tid : SV_DispatchThreadID)
{
    uint2 size;
    g_output.GetDimensions(size.x, size.y);
    if (any(tid.xy >= size)) return;
    if (g_mode >= 4)
    {
        // Top-down XZ slice; increasing slice moves the plane upward in world Y.
        float2 uv = (float2(tid.xy) + .5) / float2(size);
        int3 p = int3(min(uint(uv.x * g_grid.resolution), g_grid.resolution-1),
            min(uint(g_slice * g_grid.resolution), g_grid.resolution-1),
            min(uint(uv.y * g_grid.resolution), g_grid.resolution-1));
        SHCell cell = g_mode == 4 ? g_source[g_grid.index(p)] : g_volume[g_grid.index(p)];
        float3 average = max(float3(cell.r.x, cell.g.x, cell.b.x) * .2820947918, 0.0);
        g_output[tid.xy] = float4(average, 1);
        return;
    }
    float3 result = 0;
    for (uint i = 0; i < g_samples_per_frame; ++i)
    {
        RNG rng = RNG(tid.x + tid.y * size.x, g_sample_base + i, g_seed);
        float3 ray = g_camera.direction(float2(tid.xy) + rng.next2(), size);
        result += shade(ray, rng);
    }
    g_output[tid.xy] = float4(result / float(g_samples_per_frame), 1);
}
