// DDGI-style distance moments. Shared by final shading and material reinjection.
uniform uint g_probe_visibility; // 0 moments (default), 1 exact short ray, 2 unfiltered.
uniform uint g_distance_size;
uniform uint g_irradiance_size;
uniform float g_visibility_bias;
StructuredBuffer<float2> g_probe_distance;
StructuredBuffer<uint> g_probe_active;

float2 octahedral_uv(float3 direction)
{
    float2 uv=direction.xy/(abs(direction.x)+abs(direction.y)+abs(direction.z));
    if(direction.z<0)uv=(1-abs(uv.yx))*float2(uv.x>=0?1:-1,uv.y>=0?1:-1);
    return uv;
}

float2 sample_distance_moments(uint cell, float3 direction)
{
    uint side=g_distance_size+2;
    float2 coordinate=(octahedral_uv(direction)*.5+.5)*float(g_distance_size)+.5;
    int2 base=int2(floor(coordinate));float2 f=frac(coordinate);
    uint offset=cell*side*side;
    float2 a=lerp(g_probe_distance[offset+base.x+base.y*side],
        g_probe_distance[offset+base.x+1+base.y*side],f.x);
    float2 b=lerp(g_probe_distance[offset+base.x+(base.y+1)*side],
        g_probe_distance[offset+base.x+1+(base.y+1)*side],f.x);
    return lerp(a,b,f.y);
}

float distance_visibility_weight(float distance, float2 moments)
{
    if(distance<=moments.x)return 1;
    float variance=max(moments.y-moments.x*moments.x,0);
    float delta=distance-moments.x;
    float probability=variance/max(variance+delta*delta,1e-20);
    return probability*probability*probability;
}

float probe_visibility_weight(Grid grid, Scene scene, int3 p, Hit hit)
{
    if(g_probe_visibility==2)return 1;
    float3 probe=grid.center(p);
    float3 point=scene.offset_origin(hit);
    float3 delta=point-probe;float distance=length(delta);
    if(g_probe_visibility==1)
        return distance<=scene.ray_epsilon || scene.visible(point,-delta/distance,distance-scene.ray_epsilon) ? 1 : 0;
    point+=hit.geometric_normal*(g_visibility_bias*grid.cell_size);
    delta=point-probe;distance=length(delta);
    uint cell=grid.index(p);
    if(g_probe_active[cell]==0)return 0;
    if(distance<=scene.ray_epsilon)return 1;
    float visibility=distance_visibility_weight(distance,sample_distance_moments(cell,delta/distance));
    float3 to_probe=probe-hit.position;
    float wrap=(dot(to_probe/max(length(to_probe),1e-10),hit.normal)+1)*.5;
    float weight=(wrap*wrap+.2)*max(.05,visibility);
    weight=max(weight,1e-6);
    if(weight<.2)weight*=weight*weight/(.2*.2);
    return weight;
}

float3 sample_probe_irradiance(StructuredBuffer<float4> field, uint cell, float3 normal)
{
    uint side=g_irradiance_size+2;
    float2 coordinate=(octahedral_uv(normal)*.5+.5)*float(g_irradiance_size-1)+1;
    int2 base=int2(floor(coordinate));float2 f=frac(coordinate);
    uint offset=cell*side*side;
    float3 a=lerp(field[offset+base.x+base.y*side].xyz,field[offset+base.x+1+base.y*side].xyz,f.x);
    float3 b=lerp(field[offset+base.x+(base.y+1)*side].xyz,field[offset+base.x+1+(base.y+1)*side].xyz,f.x);
    return lerp(a,b,f.y);
}
