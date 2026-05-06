# -*- coding: utf-8 -*-
"""Convert selected rigid ducts and duct fittings to flex ducts
along the exact same path."""

__title__ = "Rigid\nTo Flex"
__doc__ = "Kijelölt rigid ductokat és fittingeket flex ductra cseréli a megrajzolt nyomvonalon."
__author__ = "signi"

import math
import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitServices")

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    BuiltInParameter,
    ElementId,
    XYZ,
    Transaction,
    Options,
    Line,
    Arc,
)
from Autodesk.Revit.DB.Mechanical import Duct, FlexDuct, FlexDuctType, MechanicalSystemType
from System.Collections.Generic import List

logger = script.get_logger()
output = script.get_output()

doc = revit.doc
uidoc = revit.uidoc

TOLERANCE = 0.001  # feet
ARC_SEGMENTS = 16  # number of segments to approximate arcs in fittings
GUIDE_POINT_DIST = 0.15  # feet (~5cm) - extra vertex distance from duct end near fittings
STRAIGHT_VERTEX_SPACING = 3.28084  # feet (~1m) - vertex spacing along straight ducts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_connectors(element):
    """Return a list of connectors for a Duct or FamilyInstance (fitting)."""
    mgr = None
    if isinstance(element, Duct):
        mgr = element.ConnectorManager
    elif hasattr(element, "MEPModel") and element.MEPModel is not None:
        mgr = element.MEPModel.ConnectorManager
    if mgr is None:
        return []
    return [c for c in mgr.Connectors]


def connector_count(element):
    return len(get_connectors(element))


def is_duct(element):
    return isinstance(element, Duct)


def is_fitting(element):
    if element.Category is None:
        return False
    return element.Category.Id == ElementId(BuiltInCategory.OST_DuctFitting)


def get_connector_shape(element):
    """Return the shape of the first connector: 'round', 'rect', or None."""
    for c in get_connectors(element):
        if c.Shape == DB.ConnectorProfileType.Round:
            return "round"
        elif c.Shape == DB.ConnectorProfileType.Rectangular:
            return "rect"
        elif c.Shape == DB.ConnectorProfileType.Oval:
            return "oval"
    return None


def points_almost_equal(p1, p2):
    return p1.DistanceTo(p2) < TOLERANCE


def get_connected_pairs(connector, selected_ids):
    """Yield (this_connector, other_connector, other_element) for connected elements."""
    if not connector.IsConnected:
        return
    for ref in connector.AllRefs:
        owner = ref.Owner
        if owner.Id == connector.Owner.Id:
            continue
        yield (connector, ref, owner)


def get_fitting_geometry_curves(element):
    """Extract geometry curves (arcs, lines) from a fitting's geometry."""
    curves = []
    try:
        opt = Options()
        opt.ComputeReferences = False
        opt.DetailLevel = DB.ViewDetailLevel.Fine
        geom = element.get_Geometry(opt)
        if geom is None:
            return curves
        for geom_obj in geom:
            if hasattr(geom_obj, "GetInstanceGeometry"):
                inst_geom = geom_obj.GetInstanceGeometry()
                if inst_geom:
                    for g in inst_geom:
                        if isinstance(g, Arc) or isinstance(g, Line):
                            curves.append(g)
            elif isinstance(geom_obj, Arc) or isinstance(geom_obj, Line):
                curves.append(geom_obj)
    except:
        pass
    return curves


def tessellate_curve(curve, num_segments):
    """Convert a curve into a list of XYZ points."""
    points = []
    for i in range(num_segments + 1):
        param = curve.GetEndParameter(0) + (curve.GetEndParameter(1) - curve.GetEndParameter(0)) * i / num_segments
        points.append(curve.Evaluate(param, False))
    return points


# ---------------------------------------------------------------------------
# Chain building
# ---------------------------------------------------------------------------

def build_chains(selected_elements):
    """Build linear chains from selected ducts/fittings.

    Returns list of (chain_elements, boundary_pairs) where boundary_pairs
    are (internal_connector, external_connector) tuples for reconnection.
    Tee fittings (>2 connectors) act as chain boundaries and are excluded.
    """
    selected_ids = set(el.Id.IntegerValue for el in selected_elements)
    visited = set()
    chains = []

    for el in selected_elements:
        if el.Id.IntegerValue in visited:
            continue
        if is_fitting(el) and connector_count(el) > 2:
            continue

        chain = []
        boundary_pairs = []
        queue = [el]

        while queue:
            current = queue.pop(0)
            cid = current.Id.IntegerValue
            if cid in visited:
                continue
            if is_fitting(current) and connector_count(current) > 2:
                for conn in get_connectors(current):
                    for my_conn, other_conn, other_el in get_connected_pairs(conn, selected_ids):
                        if other_el.Id.IntegerValue in visited or other_el.Id.IntegerValue == cid:
                            continue
                        if other_el.Id.IntegerValue in selected_ids:
                            boundary_pairs.append((other_conn, my_conn))
                continue

            visited.add(cid)
            chain.append(current)

            for conn in get_connectors(current):
                for my_conn, other_conn, other_el in get_connected_pairs(conn, selected_ids):
                    oid = other_el.Id.IntegerValue
                    if oid in visited:
                        continue
                    if oid in selected_ids:
                        if is_fitting(other_el) and connector_count(other_el) > 2:
                            boundary_pairs.append((my_conn, other_conn))
                        else:
                            queue.append(other_el)
                    else:
                        boundary_pairs.append((my_conn, other_conn))

        if chain:
            chains.append((chain, boundary_pairs))

    return chains


# ---------------------------------------------------------------------------
# Chain ordering and path extraction
# ---------------------------------------------------------------------------

def find_chain_ends(chain):
    """Find elements at the ends of a linear chain."""
    chain_ids = set(el.Id.IntegerValue for el in chain)
    ends = []
    for el in chain:
        in_chain_neighbours = 0
        for conn in get_connectors(el):
            if not conn.IsConnected:
                continue
            for ref in conn.AllRefs:
                if ref.Owner.Id.IntegerValue in chain_ids and ref.Owner.Id.IntegerValue != el.Id.IntegerValue:
                    in_chain_neighbours += 1
                    break
        if in_chain_neighbours <= 1:
            ends.append(el)
    return ends


def order_chain(chain):
    """Order chain elements linearly from one end to the other."""
    if len(chain) <= 1:
        return chain

    chain_ids = set(el.Id.IntegerValue for el in chain)
    id_to_el = {el.Id.IntegerValue: el for el in chain}

    ends = find_chain_ends(chain)
    if not ends:
        return chain
    start = ends[0]

    ordered = [start]
    visited = {start.Id.IntegerValue}

    while len(ordered) < len(chain):
        current = ordered[-1]
        found_next = False
        for conn in get_connectors(current):
            if not conn.IsConnected:
                continue
            for ref in conn.AllRefs:
                oid = ref.Owner.Id.IntegerValue
                if oid in chain_ids and oid not in visited:
                    ordered.append(id_to_el[oid])
                    visited.add(oid)
                    found_next = True
                    break
            if found_next:
                break
        if not found_next:
            break

    return ordered


def get_fitting_arc(element, entry_point):
    """Try to find the centerline arc of a fitting (elbow).
    Returns tessellated points along the arc, or None."""
    curves = get_fitting_geometry_curves(element)
    conns = get_connectors(element)
    origins = [c.Origin for c in conns]

    best_arc = None
    best_score = 1e10
    for curve in curves:
        if not isinstance(curve, Arc):
            continue
        cp0 = curve.GetEndPoint(0)
        cp1 = curve.GetEndPoint(1)
        score = min(cp0.DistanceTo(o) for o in origins) + min(cp1.DistanceTo(o) for o in origins)
        if score < best_score:
            best_score = score
            best_arc = curve

    if best_arc is None:
        return None

    arc_points = tessellate_curve(best_arc, ARC_SEGMENTS)

    # orient: make sure arc_points[0] is near entry_point
    if arc_points[-1].DistanceTo(entry_point) < arc_points[0].DistanceTo(entry_point):
        arc_points.reverse()

    return arc_points


def interpolate_straight(p0, p1, spacing):
    """Generate intermediate points between p0 and p1 at given spacing.
    Returns list including p0 and p1."""
    dist = p0.DistanceTo(p1)
    if dist <= spacing:
        return [p0, p1]
    n = int(math.ceil(dist / spacing))
    result = []
    for j in range(n + 1):
        t = float(j) / n
        result.append(XYZ(
            p0.X + (p1.X - p0.X) * t,
            p0.Y + (p1.Y - p0.Y) * t,
            p0.Z + (p1.Z - p0.Z) * t,
        ))
    return result


def extract_path_points(ordered_chain):
    """Extract ordered XYZ path points from a linearly ordered chain."""
    if not ordered_chain:
        return []

    points = []

    for i, el in enumerate(ordered_chain):
        if is_duct(el):
            loc = el.Location
            if loc is None:
                continue
            curve = loc.Curve
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)

            # orient p0 → p1 in chain direction
            if not points:
                if i + 1 < len(ordered_chain):
                    next_el = ordered_chain[i + 1]
                    next_conns = get_connectors(next_el)
                    next_origins = [c.Origin for c in next_conns]
                    d0 = min(p1.DistanceTo(o) for o in next_origins) if next_origins else 1e10
                    d1 = min(p0.DistanceTo(o) for o in next_origins) if next_origins else 1e10
                    if d1 < d0:
                        p0, p1 = p1, p0
            else:
                if p1.DistanceTo(points[-1]) < p0.DistanceTo(points[-1]):
                    p0, p1 = p1, p0
                if not points_almost_equal(p0, points[-1]):
                    points.append(p0)

            has_prev_fitting = i > 0 and is_fitting(ordered_chain[i - 1])
            has_next_fitting = i + 1 < len(ordered_chain) and is_fitting(ordered_chain[i + 1])
            duct_len = p0.DistanceTo(p1)
            direction = XYZ(p1.X - p0.X, p1.Y - p0.Y, p1.Z - p0.Z).Normalize()

            # start of this duct segment
            inner_start = p0
            inner_end = p1

            if has_prev_fitting and duct_len > GUIDE_POINT_DIST * 3:
                guide = XYZ(p0.X + direction.X * GUIDE_POINT_DIST,
                            p0.Y + direction.Y * GUIDE_POINT_DIST,
                            p0.Z + direction.Z * GUIDE_POINT_DIST)
                if not points or not points_almost_equal(p0, points[-1]):
                    points.append(p0)
                points.append(guide)
                inner_start = guide

            if has_next_fitting and duct_len > GUIDE_POINT_DIST * 3:
                rev_dir = XYZ(-direction.X, -direction.Y, -direction.Z)
                guide_end = XYZ(p1.X + rev_dir.X * GUIDE_POINT_DIST,
                                p1.Y + rev_dir.Y * GUIDE_POINT_DIST,
                                p1.Z + rev_dir.Z * GUIDE_POINT_DIST)
                inner_end = guide_end

            # add intermediate vertices along straight section
            straight_pts = interpolate_straight(inner_start, inner_end, STRAIGHT_VERTEX_SPACING)
            for sp in straight_pts:
                if not points or not points_almost_equal(sp, points[-1]):
                    points.append(sp)

            if has_next_fitting and duct_len > GUIDE_POINT_DIST * 3:
                if not points_almost_equal(p1, points[-1]):
                    points.append(p1)
            elif not points_almost_equal(p1, points[-1]):
                points.append(p1)

        elif is_fitting(el):
            conns = get_connectors(el)
            origins = [c.Origin for c in conns if c.Origin is not None]

            if len(origins) < 2:
                if origins and not points:
                    points.append(origins[0])
                continue

            # determine entry point
            if points:
                entry = points[-1]
            else:
                if i + 1 < len(ordered_chain):
                    next_el = ordered_chain[i + 1]
                    next_conns = get_connectors(next_el)
                    next_origins = [c.Origin for c in next_conns]
                    def dist_to_next(o):
                        return min(o.DistanceTo(no) for no in next_origins) if next_origins else 1e10
                    origins.sort(key=dist_to_next)
                    entry = origins[-1]
                    points.append(entry)
                else:
                    entry = origins[0]
                    points.append(entry)

            # try to get arc geometry for elbows
            arc_pts = get_fitting_arc(el, entry)
            if arc_pts:
                for ap in arc_pts:
                    if not points or not points_almost_equal(ap, points[-1]):
                        points.append(ap)
            else:
                # fallback: just add connector origins
                origins.sort(key=lambda o: o.DistanceTo(entry))
                for o in origins:
                    if not any(points_almost_equal(o, p) for p in points[-3:]):
                        points.append(o)

    # deduplicate consecutive near-identical points
    deduped = [points[0]] if points else []
    for p in points[1:]:
        if not points_almost_equal(p, deduped[-1]):
            deduped.append(p)

    return deduped


# ---------------------------------------------------------------------------
# Flex duct creation
# ---------------------------------------------------------------------------

def get_flex_duct_type_matching(doc, shape):
    """Find a FlexDuctType matching the given shape ('round' or 'rect').
    Falls back to any available type."""
    all_types = list(FilteredElementCollector(doc).OfClass(FlexDuctType))
    if not all_types:
        return None

    for ft in all_types:
        try:
            ft_conns = []
            # check shape from the type's connector definitions
            param_shape = ft.get_Parameter(BuiltInParameter.RBS_CURVETYPE_MULTISHAPE_PARAM)
            if param_shape and param_shape.HasValue:
                # 0 = round, 1 = rectangular, 2 = oval typically
                val = param_shape.AsInteger()
                if shape == "round" and val == 0:
                    return ft
                elif shape == "rect" and val == 1:
                    return ft
        except:
            pass

    # fallback: try to determine shape from the type name
    for ft in all_types:
        try:
            name = DB.Element.Name.__get__(ft).lower()
            if shape == "round" and ("round" in name or "kör" in name or "kerek" in name):
                return ft
            elif shape == "rect" and ("rect" in name or "négyszög" in name or "téglalap" in name):
                return ft
        except:
            pass

    return all_types[0]


def get_chain_size(ordered_chain):
    """Get size info from the chain. Returns dict with 'shape', 'diameter', 'width', 'height'."""
    for el in ordered_chain:
        for c in get_connectors(el):
            if c.Shape == DB.ConnectorProfileType.Round:
                return {
                    "shape": "round",
                    "diameter": c.Radius * 2.0,
                    "width": None,
                    "height": None,
                }
            elif c.Shape == DB.ConnectorProfileType.Rectangular:
                return {
                    "shape": "rect",
                    "diameter": None,
                    "width": c.Width,
                    "height": c.Height,
                }
    return None


def get_chain_system_type_id(ordered_chain):
    """Get the MechanicalSystemType id from the chain."""
    duct_sys_type_enum = None
    for el in ordered_chain:
        for c in get_connectors(el):
            try:
                dst = c.DuctSystemType
                if dst is not None:
                    duct_sys_type_enum = dst
                    break
            except:
                pass
        if duct_sys_type_enum is not None:
            break

    if duct_sys_type_enum is None:
        return None

    for mst in FilteredElementCollector(doc).OfClass(MechanicalSystemType):
        try:
            if mst.SystemClassification == duct_sys_type_enum:
                return mst.Id
        except:
            pass

    for mst in FilteredElementCollector(doc).OfClass(MechanicalSystemType):
        return mst.Id

    return None


def get_chain_level_id(ordered_chain):
    """Get the level id from the chain."""
    for el in ordered_chain:
        if is_duct(el):
            param = el.get_Parameter(BuiltInParameter.RBS_START_LEVEL_PARAM)
            if param and param.HasValue:
                return param.AsElementId()
    for el in ordered_chain:
        param = el.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM)
        if param and param.HasValue:
            return param.AsElementId()
        param = el.get_Parameter(BuiltInParameter.RBS_START_LEVEL_PARAM)
        if param and param.HasValue:
            return param.AsElementId()
    return None


def convert_chain(doc, ordered_chain, boundary_pairs, flex_type_id, size_info):
    """Create a flex duct from the chain and reconnect boundaries."""
    points = extract_path_points(ordered_chain)
    if len(points) < 2:
        logger.warning("Chain skipped: fewer than 2 path points.")
        return None, 0

    sys_type_id = get_chain_system_type_id(ordered_chain)
    level_id = get_chain_level_id(ordered_chain)

    if sys_type_id is None or level_id is None:
        logger.warning("Chain skipped: could not determine system type or level.")
        return None, 0

    point_list = List[XYZ]()
    for p in points:
        point_list.Add(p)

    flex = FlexDuct.Create(doc, sys_type_id, flex_type_id, level_id, point_list)

    # set size from original duct
    if size_info:
        if size_info["shape"] == "round" and size_info["diameter"]:
            p = flex.get_Parameter(BuiltInParameter.RBS_CURVE_DIAMETER_PARAM)
            if p and not p.IsReadOnly:
                p.Set(size_info["diameter"])
        elif size_info["shape"] == "rect":
            if size_info["width"]:
                p = flex.get_Parameter(BuiltInParameter.RBS_CURVE_WIDTH_PARAM)
                if p and not p.IsReadOnly:
                    p.Set(size_info["width"])
            if size_info["height"]:
                p = flex.get_Parameter(BuiltInParameter.RBS_CURVE_HEIGHT_PARAM)
                if p and not p.IsReadOnly:
                    p.Set(size_info["height"])

    # set tangent vectors at start/end to match duct direction
    if len(points) >= 2:
        try:
            flex.StartTangent = (points[1] - points[0]).Normalize()
        except:
            pass
        try:
            flex.EndTangent = (points[-1] - points[-2]).Normalize()
        except:
            pass

    # reconnect boundaries
    flex_conns = get_connectors(flex)
    for internal_conn, external_conn in boundary_pairs:
        best_flex_conn = None
        best_dist = 1e10
        for fc in flex_conns:
            d = fc.Origin.DistanceTo(internal_conn.Origin)
            if d < best_dist:
                best_dist = d
                best_flex_conn = fc
        if best_flex_conn is not None and best_dist < 1.0:
            try:
                best_flex_conn.ConnectTo(external_conn)
            except Exception as e:
                logger.warning("Could not reconnect boundary: {}".format(e))

    # delete original elements
    deleted = 0
    for el in ordered_chain:
        try:
            doc.Delete(el.Id)
            deleted += 1
        except Exception as e:
            logger.warning("Could not delete element {}: {}".format(el.Id, e))

    return flex, deleted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        forms.alert("Jelölj ki rigid ductokat és/vagy duct fittingeket!", exitscript=True)

    selected = []
    for eid in sel_ids:
        el = doc.GetElement(eid)
        if is_duct(el) or is_fitting(el):
            selected.append(el)

    if not selected:
        forms.alert("Nincs duct vagy fitting a kijelölésben.", exitscript=True)

    # determine shape from first duct
    chain_shape = "round"
    for el in selected:
        s = get_connector_shape(el)
        if s:
            chain_shape = s
            break

    # find matching flex duct type
    flex_type = get_flex_duct_type_matching(doc, chain_shape)
    if flex_type is None:
        forms.alert("Nincs FlexDuctType a projektben!\nTölts be egy flex duct családot először.", exitscript=True)

    chains = build_chains(selected)
    if not chains:
        forms.alert("Nem sikerült láncot építeni a kijelölt elemekből.", exitscript=True)

    total_flex = 0
    total_deleted = 0
    warnings = []

    with revit.Transaction("Rigid to Flex"):
        for chain, boundary_pairs in chains:
            ordered = order_chain(chain)
            size_info = get_chain_size(ordered)
            flex, deleted = convert_chain(doc, ordered, boundary_pairs, flex_type.Id, size_info)
            if flex is not None:
                total_flex += 1
                total_deleted += deleted
            else:
                warnings.append("Egy lánc ({} elem) nem konvertálható.".format(len(chain)))

    output.print_md("## Rigid -> Flex konverzió kész")
    output.print_md("- **{}** flex duct létrehozva".format(total_flex))
    output.print_md("- **{}** eredeti elem törölve".format(total_deleted))
    if flex_type:
        try:
            output.print_md("- Flex típus: **{}**".format(DB.Element.Name.__get__(flex_type)))
        except:
            pass
    for w in warnings:
        output.print_md("- ⚠ {}".format(w))


if __name__ == "__main__":
    main()
else:
    main()
