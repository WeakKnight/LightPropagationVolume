#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
uniform Grid g_grid;
uniform Scene g_scene;
RWStructuredBuffer<uint> g_links;
RWStructuredBuffer<SHCell> g_reflection; // Six link responses per cell: SH per unit intercepted flux.

// Five parallel rays sample each shared face. Store visible count (0..5) in
// three bits per direction. Fractional coverage avoids all-or-nothing center
// links near edges, without adding a geometry SH volume or per-frame ray work.
[shader("compute")]
[numthreads(4,4,4)]
void compute_main(uint3 tid : SV_DispatchThreadID)
{
    if (any(tid >= g_grid.resolution)) return;
    int3 p = int3(tid);
    uint packed = 0;
    for (uint i=0; i<6; ++i)
    {
        int3 axis = AXES[i], neighbor = p-axis;
        SHCell reflected = {};
        if (!g_grid.contains(neighbor))
        {
            g_reflection[g_grid.index(p)*6+i] = reflected;
            continue;
        }
        float3 tangent = axis.x != 0 ? float3(0,1,0) : float3(1,0,0);
        float3 bitangent = cross(float3(axis),tangent);
        uint visible = 0;
        for (uint ray=0; ray<5; ++ray)
        {
            float3 offset = ray == 0 ? float3(0) : .3*g_grid.cell_size*
                ((ray<=2 ? -1.0 : 1.0)*tangent + (ray%2 == 0 ? -1.0 : 1.0)*bitangent);
            Hit hit = g_scene.intersect(g_grid.center(p)+offset,-float3(axis),
                g_grid.cell_size-g_scene.ray_epsilon,g_scene.ray_epsilon);
            if (!hit.valid) ++visible;
            else
            {
                // A ray hitting a reflector is absorbed with probability 1-rho.
                // Its reflected Phi is re-emitted as Phi*cos(normal)/PI. The
                // normal faces the source cell; reflection reverses the hemisphere.
                reflected = sh_add(reflected,sh_from_lobe(hit.albedo*(INV_PI/5.0),hit.geometric_normal));
            }
        }
        packed |= visible << (3*i);
        g_reflection[g_grid.index(p)*6+i] = reflected;
    }
    g_links[g_grid.index(p)] = packed;
}
