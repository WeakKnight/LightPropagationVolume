#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
#include "geometry_volume.slang"
uniform Grid g_grid;
uniform uint g_bounces;
uniform float g_weight;
uniform float g_decay;
uniform float g_occlusion_strength;
uniform float g_bounce_strength;
uniform bool g_use_occlusion;
StructuredBuffer<GeometryCell> g_geometry;
StructuredBuffer<SHCell> g_front;
RWStructuredBuffer<SHCell> g_next;
[shader("compute")]
[numthreads(4,4,4)]
void compute_main(uint3 tid: SV_DispatchThreadID)
{
    if(any(tid>=g_grid.resolution)) return;
    int3 p=int3(tid);
    uint index=g_grid.index(p), count=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    for(uint q=0;q<g_bounces;++q)
    {
        // UE-style incremental operator: retain center, add 26 neighbors.
        // Our SH directions are light travel; UE stores the opposite convention.
        SHCell result=g_front[q*count+index];
        SHCell previous=q>0 ? g_front[(q-1)*count+index] : (SHCell)0;
        for(int z=-1;z<=1;++z) for(int y=-1;y<=1;++y) for(int x=-1;x<=1;++x)
        {
            int3 offset=int3(x,y,z), neighbor=p+offset;
            int distance2=x*x+y*y+z*z;
            if(distance2==0 || !g_grid.contains(neighbor)) continue;
            float3 toward_neighbor=float3(offset)*rsqrt(float(distance2));
            GeometryCell geometry=geometry_between(g_geometry,p,offset,g_grid.resolution+1);
            float opacity=g_use_occlusion ? saturate(dot(geometry.occlusion,cosine_lobe(toward_neighbor))*g_occlusion_strength) : 0;
            float weight=g_weight/float(distance2);
            float3 color=sh_irradiance(g_front[q*count+g_grid.index(neighbor)],-toward_neighbor)*weight*(1-opacity);
            if(q>0)
                color+=sh_irradiance(previous,toward_neighbor)*weight*geometry.material.xyz*
                    (opacity*opacity*opacity)*g_bounce_strength;
            result=sh_add(result,sh_from_direction(color,-toward_neighbor));
        }
        g_next[q*count+index]=sh_scale(result,g_decay);
    }
}
