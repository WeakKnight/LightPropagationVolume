// Bounded radiance transport predictor. RT observations are a fixed anchor,
// geometry gates cross-cell exchange. Never add the old center on every pass.
#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
#include "geometry_volume.slang"
uniform Grid g_grid;
uniform uint g_stage;
uniform float g_weight;
uniform float g_occlusion_strength;
uniform bool g_use_occlusion;
StructuredBuffer<GeometryCell> g_geometry;
StructuredBuffer<SHCell> g_captured;
StructuredBuffer<SHCell> g_input;
RWStructuredBuffer<SHCell> g_output;
[shader("compute")][numthreads(4,4,4)]
void compute_main(uint3 tid:SV_DispatchThreadID)
{
    if(any(tid>=g_grid.resolution))return;
    int3 p=int3(tid);uint count=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    uint slot=g_stage*count+g_grid.index(p);
    SHCell anchor=g_captured[slot], prediction={};
    for(int z=-1;z<=1;++z)for(int y=-1;y<=1;++y)for(int x=-1;x<=1;++x)
    {
        int d2=x*x+y*y+z*z;if(d2==0)continue;
        int3 offset=int3(x,y,z), neighbor=p-offset;
        float3 travel=float3(offset)*rsqrt(float(d2));
        // Degree-7 Lebedev quadrature on the 26 face/edge/corner directions.
        // Exact projection of every SH9 field in a homogeneous neighborhood.
        float solid_angle=4*PI*(d2==1 ? 1.0/21.0 : d2==2 ? 4.0/105.0 : 9.0/280.0);
        float visibility=0;
        SHCell incoming=anchor;
        if(g_grid.contains(neighbor))
        {
            incoming=g_input[g_stage*count+g_grid.index(neighbor)];
            GeometryCell geometry=geometry_between(g_geometry,p,-offset,g_grid.resolution+1);
            float opacity=g_use_occlusion ? saturate(dot(geometry.occlusion,cosine_lobe(-travel))*g_occlusion_strength) : 0;
            visibility=1-opacity;
        }
        // Keep signed SH reconstruction here. Clamping before re-projection
        // manufactures energy and destroys constant/low-order field invariance.
        float3 local=sh_dot(anchor,sh_basis(travel),sh_basis_l2(travel),sh_basis_last(travel));
        float3 remote=sh_dot(incoming,sh_basis(travel),sh_basis_l2(travel),sh_basis_last(travel));
        prediction=sh_add(prediction,sh_from_direction(lerp(local,remote,visibility)*solid_angle,travel));
    }
    g_output[slot]=sh_add(sh_scale(anchor,1-g_weight),sh_scale(prediction,g_weight));
}
