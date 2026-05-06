# -*- coding: utf-8 -*-
"""Convert selected rigid ducts and duct fittings to flex ducts
along the exact same path."""

__title__ = "Rigid\nTo Flex"
__doc__ = "Kijelölt rigid ductokat és fittingeket flex ductra cseréli a megrajzolt nyomvonalon."
__author__ = "signi"

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
)
from Autodesk.Revit.DB.Mechanical import Duct, FlexDuct, FlexDuctType, MechanicalSystemType
from System.Collections.Generic import List

logger = script.get_logger()
output = script.get_output()

doc = revit.doc
uidoc = revit.uidoc

TOLERANCE = 0.001  # feet


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


def is_round(element):
    """Check if all connectors are round."""
    conns = get_connectors(element)
    if not conns:
        return False
    return all(c.Shape == DB.ConnectorProfileType.Round for c in conns)


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
        # skip tee/cross fittings as chain members
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
            # skip tee/cross encountered during traversal
            if is_fitting(current) and connector_count(current) > 2:
                # record boundary: find which connector links back into the chain
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
                        # tee check
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
    """Find elements at the ends of a linear chain (elements with at most
    one in-chain neighbour)."""
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
        return chain  # fallback: cycle or unexpected topology
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

            if not points:
                # first element: figure out orientation
                if i + 1 < len(ordered_chain):
                    next_el = ordered_chain[i + 1]
                    next_conns = get_connectors(next_el)
                    next_origins = [c.Origin for c in next_conns]
                    d0 = min(p1.DistanceTo(o) for o in next_origins) if next_origins else 1e10
                    d1 = min(p0.DistanceTo(o) for o in next_origins) if next_origins else 1e10
                    if d1 < d0:
                        p0, p1 = p1, p0
                points.append(p0)
                points.append(p1)
            else:
                # orient relative to last point
                if p1.DistanceTo(points[-1]) < p0.DistanceTo(points[-1]):
                    p0, p1 = p1, p0
                if not points_almost_equal(p0, points[-1]):
                    points.append(p0)
                points.append(p1)

        elif is_fitting(el):
            conns = get_connectors(el)
            origins = [c.Origin for c in conns if c.Origin is not None]

            if not points:
                if len(origins) >= 2 and i + 1 < len(ordered_chain):
                    next_el = ordered_chain[i + 1]
                    next_conns = get_connectors(next_el)
                    next_origins = [c.Origin for c in next_conns]
                    # find which origin is closer to the next element
                    def dist_to_next(o):
                        return min(o.DistanceTo(no) for no in next_origins) if next_origins else 1e10
                    origins.sort(key=dist_to_next)
                    # the one farthest from next is our start
                    points.append(origins[-1])
                    points.append(origins[0])
                elif origins:
                    for o in origins:
                        points.append(o)
            else:
                # sort origins by distance to last known point
                origins.sort(key=lambda o: o.DistanceTo(points[-1]))
                for o in origins:
                    if not any(points_almost_equal(o, p) for p in points):
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

def get_flex_duct_type(doc):
    """Get the first available FlexDuctType."""
    collector = FilteredElementCollector(doc).OfClass(FlexDuctType)
    for ft in collector:
        return ft
    return None


def get_chain_diameter(ordered_chain):
    """Get diameter from the first duct or fitting connector in the chain."""
    for el in ordered_chain:
        for c in get_connectors(el):
            if c.Shape == DB.ConnectorProfileType.Round:
                return c.Radius * 2.0
    return None


def get_chain_system_type_id(ordered_chain):
    """Get the MechanicalSystemType id from the chain.

    FlexDuct.Create needs a MechanicalSystemType ElementId.
    We find it by matching the connector's DuctSystemType enum
    to a MechanicalSystemType in the document.
    """
    # step 1: get DuctSystemType enum from a connector
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

    # step 2: find a MechanicalSystemType whose SystemClassification matches
    for mst in FilteredElementCollector(doc).OfClass(MechanicalSystemType):
        try:
            if mst.SystemClassification == duct_sys_type_enum:
                return mst.Id
        except:
            pass

    # step 3: fallback — return the first MechanicalSystemType
    for mst in FilteredElementCollector(doc).OfClass(MechanicalSystemType):
        return mst.Id

    return None


def get_chain_level_id(ordered_chain):
    """Get the level id from the chain."""
    for el in ordered_chain:
        if is_duct(el):
            return el.get_Parameter(BuiltInParameter.RBS_START_LEVEL_PARAM).AsElementId()
    # fallback from fittings
    for el in ordered_chain:
        param = el.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM)
        if param and param.HasValue:
            return param.AsElementId()
        param = el.get_Parameter(BuiltInParameter.RBS_START_LEVEL_PARAM)
        if param and param.HasValue:
            return param.AsElementId()
    return None


def convert_chain(doc, ordered_chain, boundary_pairs, flex_type_id):
    """Create a flex duct from the chain and reconnect boundaries.
    Returns (FlexDuct, deleted_count) or (None, 0)."""
    points = extract_path_points(ordered_chain)
    if len(points) < 2:
        logger.warning("Chain skipped: fewer than 2 path points.")
        return None, 0

    diameter = get_chain_diameter(ordered_chain)
    sys_type_id = get_chain_system_type_id(ordered_chain)
    level_id = get_chain_level_id(ordered_chain)

    if sys_type_id is None or level_id is None:
        logger.warning("Chain skipped: could not determine system type or level.")
        return None, 0

    point_list = List[XYZ]()
    for p in points:
        point_list.Add(p)

    flex = FlexDuct.Create(doc, sys_type_id, flex_type_id, level_id, point_list)

    if diameter is not None:
        diam_param = flex.get_Parameter(BuiltInParameter.RBS_CURVE_DIAMETER_PARAM)
        if diam_param and not diam_param.IsReadOnly:
            diam_param.Set(diameter)

    # set tangent vectors
    if len(points) >= 2:
        start_tangent = (points[1] - points[0]).Normalize()
        end_tangent = (points[-1] - points[-2]).Normalize()
        try:
            flex.StartTangent = start_tangent
            flex.EndTangent = end_tangent
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
    # 1. Get selection
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        forms.alert("Jelölj ki rigid ductokat és/vagy duct fittingeket!", exitscript=True)

    selected = []
    skipped_rect = 0
    for eid in sel_ids:
        el = doc.GetElement(eid)
        if is_duct(el) or is_fitting(el):
            if is_round(el):
                selected.append(el)
            else:
                skipped_rect += 1
        else:
            logger.debug("Skipping non-duct element: {} ({})".format(el.Id, type(el).__name__))

    if not selected:
        msg = "Nincs megfelelő kör keresztmetszetű duct/fitting a kijelölésben."
        if skipped_rect > 0:
            msg += "\n{} db négyszögletes/ovális elem kihagyva (flex duct csak kör lehet).".format(skipped_rect)
        forms.alert(msg, exitscript=True)

    # 2. Find flex duct type
    flex_type = get_flex_duct_type(doc)
    if flex_type is None:
        forms.alert("Nincs FlexDuctType a projektben!\nTölts be egy flex duct családot először.", exitscript=True)

    # 3. Build chains
    chains = build_chains(selected)
    if not chains:
        forms.alert("Nem sikerült láncot építeni a kijelölt elemekből.", exitscript=True)

    # 4. Convert
    total_flex = 0
    total_deleted = 0
    warnings = []

    with revit.Transaction("Rigid to Flex"):
        for chain, boundary_pairs in chains:
            ordered = order_chain(chain)
            flex, deleted = convert_chain(doc, ordered, boundary_pairs, flex_type.Id)
            if flex is not None:
                total_flex += 1
                total_deleted += deleted
            else:
                warnings.append("Egy lánc ({} elem) nem konvertálható.".format(len(chain)))

    # 5. Report
    output.print_md("## Rigid → Flex konverzió kész")
    output.print_md("- **{}** flex duct létrehozva".format(total_flex))
    output.print_md("- **{}** eredeti elem törölve".format(total_deleted))
    if skipped_rect > 0:
        output.print_md("- **{}** négyszögletes elem kihagyva".format(skipped_rect))
    for w in warnings:
        output.print_md("- ⚠ {}".format(w))


if __name__ == "__main__":
    main()
else:
    main()
