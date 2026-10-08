#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
uniform Grid g_grid;
uniform float g_decay;
uniform bool g_use_occlusion;
uniform uint g_bounces;
StructuredBuffer<SHCell> g_front; // [material bounce][cell], current spatial front only.
StructuredBuffer<uint> g_links;
StructuredBuffer<SHCell> g_reflection;
RWStructuredBuffer<SHCell> g_next;
RWStructuredBuffer<SHCell> g_accumulated;
static const float MAIN_SOLID_ANGLE = .4006696846;
static const float SIDE_SOLID_ANGLE = .4234313544;

// Integrate the flux routed to one neighboring cell by the five-face stencil.
float3 outgoing_flux(SHCell source, int3 direction)
{
    float3 flux = sh_evaluate(source,float3(direction))*MAIN_SOLID_ANGLE;
    for (uint face=0; face<6; ++face)
    {
        int3 side=AXES[face];
        if (dot(float3(side),float3(direction)) != 0) continue;
        flux += sh_evaluate(source,normalize(float3(direction)+.5*float3(side)))*SIDE_SOLID_ANGLE;
    }
    return flux;
}

SHCell budgeted_source(SHCell source)
{
    // Clamping SH ringing to nonnegative radiance can create energy. Bound the
    // SAME six-face quadrature used for transmission/reflection by the stored
    // integral (coefficient zero). This is a local physical budget, not a gain
    // fitted to a camera or reference image.
    float3 outgoing=0;
    for(uint i=0;i<6;++i)outgoing+=outgoing_flux(source,AXES[i]);
    float3 budget=max(float3(source.r.x,source.g.x,source.b.x)*3.5449077018,0);
    return sh_color_scale(source,min(float3(1),budget/max(outgoing,float3(1e-20))));
}

[shader("compute")]
[numthreads(4,4,4)]
void compute_main(uint3 tid : SV_DispatchThreadID)
{
    if (any(tid >= g_grid.resolution)) return;
    int3 p=int3(tid);
    uint index=g_grid.index(p), count=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    SHCell total={};
    for (uint bounce=0; bounce<g_bounces; ++bounce)
    {
        SHCell result={};
        SHCell previous = bounce>0 ? budgeted_source(g_front[(bounce-1)*count+index]) : (SHCell)0;
        for (uint i=0; i<6; ++i)
        {
            int3 d=AXES[i], neighbor=p-d;
            if (!g_grid.contains(neighbor)) continue;
            float visibility=g_use_occlusion ? float((g_links[index]>>(3*i))&7u)/5.0 : 1.0;
            if (visibility>0)
            {
                SHCell source=budgeted_source(g_front[bounce*count+g_grid.index(neighbor)]);
                float3 flux=sh_evaluate(source,float3(d))*(MAIN_SOLID_ANGLE*visibility);
                result=sh_add(result,sh_from_lobe(flux*INV_PI,float3(d)));
                for (uint face=0; face<6; ++face)
                {
                    int3 side=AXES[face];
                    if (dot(float3(side),float3(d)) != 0) continue;
                    float3 direction=normalize(float3(d)+.5*float3(side));
                    flux=sh_evaluate(source,direction)*(SIDE_SOLID_ANGLE*visibility);
                    result=sh_add(result,sh_from_lobe(flux*INV_PI,float3(side)));
                }
            }
            if (bounce>0 && g_use_occlusion)
            {
                // Only blocked outgoing flux from the PREVIOUS material order
                // becomes this order. Coverage, albedo and normal are already
                // in the cached response. Never reflect the accumulated history.
                SHCell response=g_reflection[index*6+i];
                if (max(response.r.x,max(response.g.x,response.b.x))>0)
                    result=sh_add(result,sh_color_scale(response,outgoing_flux(previous,-d)));
            }
        }
        result=sh_scale(result,g_decay);
        g_next[bounce*count+index]=result;
        total=sh_add(total,result);
    }
    // One owner per cell adds every new front exactly once, without atomics.
    g_accumulated[index]=sh_add(g_accumulated[index],total);
}
