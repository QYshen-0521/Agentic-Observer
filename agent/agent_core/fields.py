"""Bounded fibre-grid phase search; every returned field uses exact geometry."""
import math

from .geometry import shift_altaz, tangent_offsets


def phase_fields(grid, centers, anchors, neighbours, visible, altaz,
                 value, completion_seconds, fast_level, edge_margin):
    """Screen translated tangent planes cheaply, then reproject the shortlist.

    The planar scores only propose pointings. Returned assignments and margins
    always come from the pointing's own spherical tangent plane, including near
    the zenith. Absolute value and throughput each retain twelve proposals.
    """
    proposals = []
    n_anchors = 2 if fast_level >= 1 else 6
    middle = sorted({(grid.side - 1) // 2, grid.side // 2})
    fibers = range(grid.n) if fast_level < 2 else tuple(
        row * grid.side + col for row in middle for col in middle)
    half, pitch, glass_half = grid.fov / 2, grid.pitch, grid.glass / 2
    phases = (-pitch / 4, 0.0, pitch / 4)
    for tried, (_, anchor) in enumerate(anchors):
        if tried >= n_anchors + 4 or (tried >= n_anchors and proposals):
            break
        a_alt, a_az = altaz(anchor)
        near = [j for j in neighbours(anchor, 1.6 * grid.fov) if j in visible]
        projected = []
        for j in near:
            v = value(j)
            if v <= 0:
                continue
            point = tangent_offsets(*altaz(j), a_alt, a_az)
            if point is not None:
                projected.append((*point, j, v, v / completion_seconds(j)))
        for fiber in fibers:
            north, east = centers[fiber]
            for dy in phases:
                for dx in phases:
                    cn, ce = -north + dy, -east + dx
                    cells, rates = {}, {}
                    for tn, te, j, v, rate in projected:
                        y, x = tn - cn, te - ce
                        if abs(y) > half or abs(x) > half:
                            continue
                        row = min(max(int(math.floor(y / pitch + grid.side / 2)), 0), grid.side - 1)
                        col = min(max(int(math.floor(x / pitch + grid.side / 2)), 0), grid.side - 1)
                        fi = row * grid.side + col
                        fn, fe = centers[fi]
                        margin = glass_half - max(abs(y - fn), abs(x - fe))
                        if margin < 0:
                            continue
                        weight = 1.0 if margin >= edge_margin else 0.65
                        cells[fi] = max(cells.get(fi, 0), v * weight)
                        rates[fi] = max(rates.get(fi, 0), rate * weight)
                    if cells:
                        proposals.append((sum(cells.values()), sum(rates.values()),
                                          a_alt, a_az, cn, ce, near))
    indices = set(sorted(range(len(proposals)), key=lambda i: -proposals[i][0])[:12])
    indices.update(sorted(range(len(proposals)), key=lambda i: -proposals[i][1])[:12])
    by_anchor = {}
    for i, proposal in enumerate(proposals):
        key = proposal[2:4]
        if key not in by_anchor or proposal[0] > proposals[by_anchor[key]][0]:
            by_anchor[key] = i
    indices.update(by_anchor.values())
    fields = []
    for index in sorted(indices):
        _, _, a_alt, a_az, cn, ce, near = proposals[index]
        c_alt, c_az = shift_altaz(a_alt, a_az, cn, ce)
        # The planner supplies the stricter public altitude check.
        if not 0 < c_alt <= 89:
            continue
        c_alt, c_az = round(c_alt, 4), round(c_az, 4) % 360
        chosen = {}
        for j in near:
            v = value(j)
            if v <= 0:
                continue
            point = tangent_offsets(*altaz(j), c_alt, c_az)
            if point is None:
                continue
            fi, margin = grid.classify(*point)
            if fi is not None:
                score = v * (1.0 if margin >= edge_margin else 0.65)
                chosen.setdefault(fi, []).append((score, j, margin))
        if chosen:
            for choices in chosen.values():
                choices.sort(reverse=True)
                del choices[3:]
            fields.append((sum(v[0][0] for v in chosen.values()), c_alt, c_az, chosen))
    return fields
