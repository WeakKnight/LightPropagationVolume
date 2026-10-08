"""Static DDGI-style octahedral distance moments (geometry cache, not lighting)."""
import numpy as np
import slangpy as spy

DISTANCE_SIZE = 16
DISTANCE_RAYS = 512
DISTANCE_FILTER_TAPS = 64
DISTANCE_EXPONENT = 50
IRRADIANCE_SIZE = 17


def octahedral_directions(size=DISTANCE_SIZE, vertices=False):
    """Interior texel normals plus a mirrored one-texel octahedral border."""
    side = size+2
    y, x = np.indices((side, side))
    bx = (x == 0) | (x == side-1)
    by = (y == 0) | (y == side-1)
    source_x = np.where(bx, np.where(x == 0, size, 1), x)
    source_y = np.where(by, np.where(y == 0, size, 1), y)
    source_x = np.where(by & ~bx, side-1-x, source_x)
    source_y = np.where(bx & ~by, side-1-y, source_y)
    source_x = np.where(bx & ~by, np.where(x == 0, 1, size), source_x)
    source_y = np.where(by & ~bx, np.where(y == 0, 1, size), source_y)
    coordinates = np.stack((source_x, source_y), -1).reshape(-1, 2)
    # An odd vertex grid includes all six cardinal directions exactly. This
    # prevents normal interpolation from introducing flat-plane self light.
    uv = (coordinates-1)/(size-1)*2-1 if vertices else (coordinates-.5)/size*2-1
    directions = np.column_stack((uv, 1-np.abs(uv).sum(1)))
    lower = directions[:, 2] < 0
    directions[lower, :2] = (1-np.abs(uv[lower, ::-1]))*np.where(uv[lower] >= 0, 1, -1)
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    return directions


def distance_layout():
    ids = np.arange(DISTANCE_RAYS)
    z = 1-2*(ids+.5)/DISTANCE_RAYS
    phi = ids*np.pi*(3-np.sqrt(5))
    rays = np.column_stack((np.sqrt(1-z*z)*np.cos(phi), np.sqrt(1-z*z)*np.sin(phi), z))
    directions = octahedral_directions()
    cosine = directions @ rays.T
    indices = np.argsort(-cosine, axis=1)[:, :DISTANCE_FILTER_TAPS]
    weights = np.maximum(np.take_along_axis(cosine, indices, axis=1), 0)**DISTANCE_EXPONENT
    weights /= weights.sum(1)[:, None]
    # 64 of 512 uniform rays cover the cos^50 lobe; discarded tail is tiny.
    return (np.column_stack((rays, np.zeros(len(rays)))).astype(np.float32),
            indices.astype(np.uint32), weights.astype(np.float32))


class ProbeVisibility:
    def __init__(self, device):
        self.device = device
        self.kernels = {name: device.create_compute_kernel(device.load_program('probe_distance.slang', [name]))
                        for name in ('trace_main', 'blend_main', 'classify_main')}
        rays, indices, weights = distance_layout()
        self.rays = self.buffer(rays, 'directions')
        self.indices = self.buffer(indices, 'filter_indices')
        self.weights = self.buffer(weights, 'filter_weights')
        self.signature = None
        self.builds = 0

    def buffer(self, data, name):
        return self.device.create_buffer(data=np.ascontiguousarray(data),
            usage=spy.BufferUsage.shader_resource | spy.BufferUsage.unordered_access, label='LPV.distance.'+name)

    def update(self, encoder, scene, grid):
        signature = (id(scene), grid.resolution)
        if signature == self.signature:
            return
        self.device.wait()
        cells = grid.resolution**3
        self.depths = self.buffer(np.zeros((cells, DISTANCE_RAYS), np.float32), 'ray_distances')
        self.moments = self.buffer(np.zeros((cells, (DISTANCE_SIZE+2)**2, 2), np.float32), 'moments')
        self.active = self.buffer(np.zeros(cells, np.uint32), 'active')
        values = {'g_grid': grid.shader_values(), 'g_distance_rays': DISTANCE_RAYS,
                  'g_distance_size': DISTANCE_SIZE, 'g_filter_taps': DISTANCE_FILTER_TAPS,
                  'g_depths': self.depths}
        self.kernels['trace_main'].dispatch(thread_count=[cells*DISTANCE_RAYS, 1, 1], vars={**values,
            'g_scene': {'tlas': scene.tlas, **scene.buffers, 'ray_epsilon': scene.epsilon}, 'g_ray_directions': self.rays}, command_encoder=encoder)
        encoder.global_barrier()
        self.kernels['blend_main'].dispatch(thread_count=[cells*(DISTANCE_SIZE+2)**2, 1, 1], vars={**values,
            'g_filter_indices': self.indices, 'g_filter_weights': self.weights, 'g_moments': self.moments}, command_encoder=encoder)
        encoder.global_barrier()
        self.kernels['classify_main'].dispatch(thread_count=[cells, 1, 1], vars={**values,
            'g_active': self.active}, command_encoder=encoder)
        encoder.global_barrier()
        self.signature = signature
        self.builds += 1

    def shader_values(self):
        return {'g_distance_size': DISTANCE_SIZE, 'g_probe_distance': self.moments, 'g_probe_active': self.active}
