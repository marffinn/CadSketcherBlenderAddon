############### Operators ###############
import bpy, bgl, gpu
from gpu_extras.batch import batch_for_shader
from bpy.types import Operator
from . import global_data, functions, class_defines, convertors
from bpy.props import (
    IntProperty,
    StringProperty,
    FloatProperty,
    FloatVectorProperty,
    EnumProperty,
    BoolProperty,
)
import math
from mathutils import Vector, Matrix
from mathutils.geometry import intersect_line_plane, distance_point_to_plane

import functools
import logging

logger = logging.getLogger(__name__)


def draw_selection_buffer(context):
    # Draw elements offscreen
    region = context.region

    # create offscreen
    width, height = region.width, region.height
    offscreen = global_data.offscreen = gpu.types.GPUOffScreen(width, height)

    with offscreen.bind():
        bgl.glClearColor(0.0, 0.0, 0.0, 0.0)
        bgl.glClear(bgl.GL_COLOR_BUFFER_BIT)

        entities = list(context.scene.sketcher.entities.all)
        for e in reversed(entities):
            if e.slvs_index in global_data.ignore_list:
                continue
            if not hasattr(e, "draw_id"):
                continue
            if not e.is_selectable(context):
                continue
            e.draw_id(context)


def ensure_selection_texture(context):
    if not global_data.redraw_selection_buffer:
        return

    draw_selection_buffer(context)
    global_data.redraw_selection_buffer = False


# TODO: Avoid to always update batches and selection texture


def update_elements(context, force=False):
    entities = list(context.scene.sketcher.entities.all)
    msg = ""
    for e in reversed(entities):
        if not hasattr(e, "update"):
            continue
        if not force and not e.is_dirty:
            continue

        msg += "\n - " + str(e) + str(e.is_dirty)
        e.update()

    if msg:
        logger.debug("Update geometry batches:" + msg)


def draw_elements(context):
    for e in context.scene.sketcher.entities.all:
        if hasattr(e, "draw"):
            e.draw(context)


def draw_cb():
    context = bpy.context

    prefs = functions.get_prefs()
    update_elements(context, force=prefs.force_redraw)
    draw_elements(context)

    global_data.redraw_selection_buffer = True


class HighlightElement:
    """
    Mix-in class to highlight the element this operator acts on. The element can
    either be an entity or a constraint. The element has to be specified by an index
    property for entities and additionaly with a type property for constraints.

        index: IntProperty
        type: StringProperty


    Note that this defines the invoke and description functions, an operator that
    defines one of those has to manually make a call to either of the following:

        self.handle_highlight_active(context) -> from invoke()
        cls.handle_highlight_hover(context, properties) -> from description()


    Settings:
    highlight_hover -> highlights the element as soon as the tooltip is shown
    highlight_active -> highlights the element when the operator is invoked
    highlight_members -> highlights the element members e.g. the entities dependencies or
                the entities the constraint acts on
    """

    highlight_hover: BoolProperty(name="Highlight Hover")
    highlight_active: BoolProperty(name="Highlight Hover")
    highlight_members: BoolProperty(name="Highlight Members")

    @classmethod
    def _do_highlight(cls, context, properties):
        if not properties.is_property_set("index"):
            return cls.__doc__

        index = properties.index
        members = properties.highlight_members

        if hasattr(properties, "type") and properties.is_property_set("type"):
            type = properties.type
            c = context.scene.sketcher.constraints.get_from_type_index(type, index)
            if members:
                global_data.highlight_entities.extend(c.entities())
            else:
                global_data.highlight_constraint = c
        else:
            if members:
                e = context.scene.sketcher.entities.get(index)
                global_data.highlight_entities.extend(e.dependencies())
            else:
                # Set hover so this could be used as selection
                global_data.hover = properties.index

        context.area.tag_redraw()
        return cls.__doc__

    def handle_highlight_active(self, context):
        properties = self.properties
        if properties.highlight_active:
            self._do_highlight(context, properties)

    @classmethod
    def handle_highlight_hover(cls, context, properties):
        if properties.highlight_hover:
            cls._do_highlight(context, properties)

    @classmethod
    def description(cls, context, properties):
        cls.handle_highlight_hover(context, properties)

    def invoke(self, context, event):
        self.handle_highlight_active(context)
        return self.execute(context)


class View3D_OT_slvs_register_draw_cb(Operator):
    bl_idname = "view3d.slvs_register_draw_cb"
    bl_label = "Register Draw Callback"

    def execute(self, context):
        global_data.draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            draw_cb, (), "WINDOW", "POST_VIEW"
        )

        return {"FINISHED"}


class View3D_OT_slvs_unregister_draw_cb(Operator):
    bl_idname = "view3d.slvs_unregister_draw_cb"
    bl_label = ""

    def execute(self, context):
        global_data.draw_handler.remove_handle()
        return {"FINISHED"}


def deselect_all(context):
    global_data.selected.clear()


def entities_3d(context):
    for e in context.scene.sketcher.entities.all:
        if hasattr(e, "sketch"):
            continue
        yield e


def select_all(context):
    sketch = context.scene.sketcher.active_sketch
    if sketch:
        generator = sketch.sketch_entities(context)
    else:
        generator = entities_3d(context)

    for e in generator:
        if e.selected:
            continue
        if not e.is_visible(context):
            continue
        if not e.is_active(context.scene.sketcher.active_sketch):
            continue
        e.selected = True


class View3D_OT_slvs_select(Operator, HighlightElement):
    """
    Select an entity

    Either the entity specified by the index property or the hovered index
    if the index property is not set

    """

    bl_idname = "view3d.slvs_select"
    bl_label = "Select Solvespace Entities"

    index: IntProperty(name="Index", default=-1)
    # TODO: Add selection modes

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        index = self.index if self.properties.is_property_set("index") else global_data.hover
        if index != -1:
            entity = context.scene.sketcher.entities.get(index)
            entity.selected = not entity.selected
        else:
            deselect_all(context)
        context.area.tag_redraw()
        return {"FINISHED"}


class View3D_OT_slvs_select_all(Operator):
    """Select / Deselect all entities"""

    bl_idname = "view3d.slvs_select_all"
    bl_label = "Select / Deselect Entities"

    deselect: BoolProperty(name="Deselect")

    def execute(self, context):
        if self.deselect:
            deselect_all(context)
        else:
            select_all(context)
        context.area.tag_redraw()
        return {"FINISHED"}


class View3D_OT_slvs_context_menu(Operator, HighlightElement):
    """Show element's settings"""

    bl_idname = "view3d.slvs_context_menu"
    bl_label = "Solvespace Context Menu"

    type: StringProperty(name="Type", options={'SKIP_SAVE'})
    index: IntProperty(name="Index", default=-1, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        is_entity = True
        entity_index = None
        constraint_index = None
        element = None

        # Constraints
        if self.properties.is_property_set("type"):
            constraint_index = self.index
            constraints = context.scene.sketcher.constraints
            element = constraints.get_from_type_index(self.type, self.index)
            is_entity = False
        else:
            # Entities
            entity_index = self.index if self.properties.is_property_set("index") else global_data.hover

            if entity_index != -1:
                element = context.scene.sketcher.entities.get(entity_index)

        def draw_context_menu(self, context):
            col = self.layout.column()

            if not element:
                col.label(text="Nothing hovered")
                return

            col.label(text="Type: " + type(element).__name__)

            if is_entity:
                if functions.get_prefs().show_debug_settings:
                    col.label(text="Index: " + str(element.slvs_index))
                col.label(text="Is Origin: " + str(element.origin))
                col.separator()
                col.prop(element, "visible")
                col.prop(element, "fixed")
                col.prop(element, "construction")

            elif element.failed:
                col.label(text="Failed", icon="ERROR")
            col.separator()

            if hasattr(element, "draw_props"):
                element.draw_props(col)
                col.separator()

            # Delete operator
            if is_entity:
                col.operator(View3D_OT_slvs_delete_entity.bl_idname, icon='X').index = element.slvs_index
            else:
                props = col.operator(View3D_OT_slvs_delete_constraint.bl_idname, icon='X')
                props.type = element.type
                props.index = constraint_index

        context.window_manager.popup_menu(draw_context_menu)
        return {"FINISHED"}

class View3D_OT_slvs_show_solver_state(Operator):
    """Show details about solver status"""

    bl_idname = "view3d.slvs_show_solver_state"
    bl_label = "Solver Status"

    index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        index = self.index
        if index == -1:
            return {"CANCELLED"}

        def draw_item(self, context):
            layout = self.layout
            sketch = context.scene.sketcher.entities.get(index)
            state = sketch.get_solver_state()

            row = layout.row(align=True)
            row.alignment = "LEFT"
            row.label(text=state.name, icon=state.icon)

            layout.separator()
            layout.label(text=state.description)

        context.window_manager.popup_menu(draw_item)
        return {"FINISHED"}


from .solver import Solver, solve_system


class View3D_OT_slvs_solve(Operator):
    bl_idname = "view3d.slvs_solve"
    bl_label = "Solve"

    all: BoolProperty(name="Solve All", options={"SKIP_SAVE"})

    def execute(self, context):
        sketch = context.scene.sketcher.active_sketch
        solver = Solver(context, sketch, all=self.all)
        ok = solver.solve()

        # Keep messages simple, sketches are marked with solvestate
        if ok:
            self.report({"INFO"}, "Successfully solved")
        else:
            self.report({"WARNING"}, "Solver failed")

        context.area.tag_redraw()
        return {"FINISHED"}


def add_point(context, pos, name=""):
    data = bpy.data
    ob = data.objects.new(name, None)
    ob.location = pos
    context.collection.objects.link(ob)
    return ob


class View3D_OT_slvs_tweak(Operator):
    """Tweak the hovered element"""

    bl_idname = "view3d.slvs_tweak"
    bl_label = "Tweak Solvespace Entities"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return True

    def invoke(self, context, event):
        index = global_data.hover
        # TODO: hover should be -1 if nothing is hovered, not None!
        if index == None or index == -1:
            return {"CANCELLED"}

        entity = context.scene.sketcher.entities.get(index)
        self.entity = entity

        coords = (event.mouse_region_x, event.mouse_region_y)
        origin, view_vector = functions.get_picking_origin_dir(context, coords)

        if not hasattr(entity, "closest_picking_point"):
            if not hasattr(entity, "sketch"):
                self.report(
                    {"WARNING"}, "Cannot tweak element of type {}".format(type(entity))
                )
                return {"CANCELLED"}

            # For 2D entities it should be enough precise to get picking point from intersection with workplane
            wp = entity.sketch.wp
            coords = (event.mouse_region_x, event.mouse_region_y)
            origin, dir = functions.get_picking_origin_dir(context, coords)
            end_point = dir * context.space_data.clip_end + origin
            pos = intersect_line_plane(origin, end_point, wp.p1.location, wp.normal)
        else:
            pos = entity.closest_picking_point(origin, view_vector)

        # find the depth
        self.depth = (pos - origin).length

        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            context.window.cursor_modal_restore()
            return {"FINISHED"}

        context.window.cursor_modal_set("HAND")

        if event.type == "MOUSEMOVE":
            entity = self.entity
            coords = (event.mouse_region_x, event.mouse_region_y)

            # Get tweaking position
            origin, dir = functions.get_picking_origin_dir(context, coords)

            if hasattr(entity, "sketch"):
                wp = entity.wp
                end_point = dir * context.space_data.clip_end + origin
                pos = intersect_line_plane(origin, end_point, wp.p1.location, wp.normal)
            else:
                pos = dir * self.depth + origin

            sketch = context.scene.sketcher.active_sketch
            solver = Solver(context, sketch)
            solver.tweak(entity, pos)
            retval = solver.solve(report=False)

            # NOTE: There's no blocking cursor
            # also solving frequently returns an error while tweaking which causes flickering
            # if retval != 0:
            # context.window.cursor_modal_set("WAIT")
            # self.report({'WARNING'}, "Cannot solve sketch, error: {}".format(retval))

            context.area.tag_redraw()

        return {"RUNNING_MODAL"}


def write_selection_buffer_image(image_name):
    offscreen = global_data.offscreen
    width, height = offscreen.width, offscreen.height
    buffer = bgl.Buffer(bgl.GL_FLOAT, width * height * 4)
    with offscreen.bind():
        bgl.glReadPixels(0, 0, width, height, bgl.GL_RGBA, bgl.GL_FLOAT, buffer)

    if not image_name in bpy.data.images:
        bpy.data.images.new(image_name, width, height)
    image = bpy.data.images[image_name]
    image.scale(width, height)
    image.pixels = buffer
    return image


class VIEW3D_OT_slvs_write_selection_texture(Operator):
    """Write selection texture to image for debuging"""

    bl_idname = "view3d.slvs_write_selection_texture"
    bl_label = "Write selection texture"

    def execute(self, context):
        if context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "View3D not found, cannot run operator")
            return {"CANCELLED"}

        if not global_data.offscreen:
            self.report({'WARNING'}, "Selection texture is not available")
            return {'CANCELLED'}

        image = write_selection_buffer_image("selection_buffer")
        self.report({"INFO"}, "Wrote buffer to image: {}".format(image.name))

        return {"FINISHED"}


# NOTE: The draw handler has to be registered before this has any effect, currently it's possible that
# entities are first created with an entity that was hovered in the previous state
# Not sure if it's possible to force draw handlers...
# Also note that a running modal operator might prevent redraws, avoid returning running_modal
def ignore_hover(entity):
    ignore_list = global_data.ignore_list
    ignore_list.append(entity.slvs_index)


# TODO: could probably check entity type only through index, instead of getting the entity first...
def get_hovered(context, *types):
    hovered = global_data.hover
    entity = None

    if hovered != -1:
        entity = context.scene.sketcher.entities.get(hovered)
        if type(entity) in types:
            return entity
    return None


def format_types(types):
    entity_names = ", ".join([e.__name__ for e in types])
    return "[" + entity_names + "]"


def state_desc(name, desc, types):
    type_desc = ""
    if types:
        type_desc = "Types: " + format_types(types)
    return " ".join((name + ":", desc, type_desc))


def stateful_op_desc(base, *state_descs):
    states = ""
    length = len(state_descs)
    for i, state in enumerate(state_descs):
        states += " - {}{}".format(
            state, ("  \n" if i < length - 1 else "")
        )
    desc = "{}  \n  \nStates:  \n{}".format(base, states)
    return desc


numeric_events = (
    "ZERO",
    "ONE",
    "TWO",
    "THREE",
    "FOUR",
    "FIVE",
    "SIX",
    "SEVEN",
    "EIGHT",
    "NINE",
    "PERIOD",
    "NUMPAD_0",
    "NUMPAD_1",
    "NUMPAD_2",
    "NUMPAD_3",
    "NUMPAD_4",
    "NUMPAD_5",
    "NUMPAD_6",
    "NUMPAD_7",
    "NUMPAD_8",
    "NUMPAD_9",
    "NUMPAD_PERIOD",
    "MINUS",
    "NUMPAD_MINUS",
)

def get_evaluated_obj(context, object):
    return object.evaluated_get(context.evaluated_depsgraph_get())

def get_mesh_element(context, coords, vertex=False, edge=False, face=False, threshold=0.5, object=None):
    from bpy_extras import view3d_utils

    # get the ray from the viewport and mouse
    region = context.region
    rv3d = context.region_data
    view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coords)
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coords)
    depsgraph = context.view_layer.depsgraph
    scene = context.scene
    result, loc, _normal, face_index, ob, _matrix = scene.ray_cast(depsgraph, ray_origin, view_vector)

    if object:
        # Alternatively do a object raycast if we know the object already
        tree = functions.bvhtree_from_object(ob)
        loc, _normal, face_index, _distance = tree.ray_cast(ray_origin, view_vector)
        result = loc != None
        ob = object

    if not result:
        return None, None, None

    obj_eval = get_evaluated_obj(context, ob)

    closest_type = ""
    closest_dist = None

    loc = obj_eval.matrix_world.inverted() @ loc
    me = obj_eval.data
    polygon = me.polygons[face_index]

    def get_closest(deltas):
        index_min = min(range(len(deltas)), key=deltas.__getitem__)
        if deltas[index_min] > threshold:
            return None, None
        return index_min, deltas[index_min]

    def is_closer(distance, min_distance):
        if min_distance is None:
            return True
        if distance < min_distance:
            return True
        return False

    if vertex:
        i, dist = get_closest([(me.vertices[i].co - loc).length for i in polygon.vertices])
        if i is not None:
            closest_type = "VERTEX"
            closest_index = polygon.vertices[i]
            closest_dist = dist

    if edge:
        face_edge_map = {ek: me.edges[i] for i, ek in enumerate(me.edge_keys)}
        i, dist = get_closest(
            [(((me.vertices[start].co + me.vertices[end].co)/2)-loc).length for start, end in polygon.edge_keys]
            )
        if i is not None and is_closer(dist, closest_dist):
            closest_type = "EDGE"
            closest_index = face_edge_map[polygon.edge_keys[i]].index
            closest_dist = dist

    if face:
        # Check if face midpoint is closest
        if is_closer((polygon.center - loc).length, closest_dist):
            closest_type = "FACE"
            closest_index = face_index

    if closest_type:
        return ob, closest_type, closest_index
    return ob, bpy.types.Object, None

def to_list(val):
    if val == None:
        return []
    if type(val) in (list, tuple):
        return list(val)
    return [val,]

def _get_pointer_get_set(index):
    @property
    def func(self):
        return self.get_state_pointer(index=index)

    @func.setter
    def setter(self, value):
        self.set_state_pointer(value, index=index)
    return func, setter

mesh_element_types = bpy.types.MeshVertex, bpy.types.MeshEdge, bpy.types.MeshPolygon

class StatefulOperator:
    state_index: IntProperty(options={"HIDDEN", "SKIP_SAVE"})
    wait_for_input: BoolProperty(options={"HIDDEN", "SKIP_SAVE"}, default=True)
    continuose_draw: BoolProperty(name="Continuose Draw", default=False)

    executed = False
    _state_data = {}
    _last_coords = Vector((0, 0))
    _numeric_input = {}

    @classmethod
    def _has_global_object(cls):
        states = cls.get_states_definition()
        return any([s.pointer == "object" for s in states])

    def _get_global_object_index(cls):
        states = cls.get_states_definition()
        object_in_list = [s.pointer == "object" for s in states]
        if not any(object_in_list):
            return None
        return object_in_list.index(True)

    @classmethod
    def register_properties(cls):
        states = cls.get_states_definition()
        annotations = cls.__annotations__.copy()

        # Have some specific logic: pointer name "object" is used as global object
        # otherwise define ob_name for each element
        has_global_object = cls._has_global_object()

        for i, s in enumerate(states):
            pointer_name = s.pointer
            types = s.types

            if not pointer_name:
                continue

            if pointer_name in annotations.keys():
                # Skip pointers that have a property defined
                # Note: pointer might not need implicit props, thus no need for getter/setter
                return

            if hasattr(cls, pointer_name):
                raise NameError("Cannot register state pointer getter/setter, class {} already has attribute of name {}".format(cls, pointer_name))

            get, set = _get_pointer_get_set(i)
            setattr(cls, pointer_name, get)
            # Note: keep state pointers read-only, only set with set_state_pointer()

        for a in annotations.keys():
            if hasattr(cls, a):
                raise NameError("Cannot register implicit pointer properties, class {} already has attribute of name {}".format(cls, a))
        setattr(cls, "__annotations__", annotations)


    def state_property(self, state_index):
        return None

    def get_state_pointer(self, index=None, implicit=False):
        # Creates pointer value from it's implicitly stored props
        if index is None:
            index = self.state_index

        state = self.get_states_definition()[index]
        pointer_name = state.pointer
        data = self._state_data.get(index, {})
        if not "type" in data.keys():
            return None

        pointer_type = data["type"]
        if not pointer_type:
            return None

        if pointer_type in (bpy.types.Object, *mesh_element_types):
            if self._has_global_object():
                global_ob_index = self._get_global_object_index()
                obj_name = self._state_data[global_ob_index]["object_name"]
            else:
                obj_name = data["object_name"]
            obj = get_evaluated_obj(bpy.context, bpy.data.objects[obj_name])

        if pointer_type in mesh_element_types:
            index = data["mesh_index"]

        if pointer_type == bpy.types.Object:
            if implicit:
                return obj_name
            return obj

        elif pointer_type == bpy.types.MeshVertex:
            if implicit:
                return obj_name, index
            return obj.data.vertices[index]

        elif pointer_type == bpy.types.MeshEdge:
            if implicit:
                return obj_name, index
            return obj.data.edges[index]

        elif pointer_type == bpy.types.MeshPolygon:
            if implicit:
                return obj_name, index
            return obj.data.polygons[index]


    def set_state_pointer(self, values, index=None, implicit=False):
        # handles type specific setters
        if index is None:
            index = self.state_index

        state = self.get_states_definition()[index]
        pointer_name = state.pointer
        data = self._state_data.get(index, {})

        pointer_type = data["type"]

        def get_value(index):
            if values == None:
                return None
            return values[index]

        if pointer_type == bpy.types.Object:
            if implicit:
                val = get_value(0)
            else:
                val = get_value(0).name
            data["object_name"] = val
            return True

        elif pointer_type in mesh_element_types:
            obj_name = get_value(0) if implicit else get_value(0).name
            if self._has_global_object():
                self._state_data[self._get_global_object_index()]["object_name"] = obj_name
            else:
                data["object_name"] = obj_name

            data["mesh_index"] = get_value(1) if implicit else get_value(1).index
            return True


    def pick_element(self, context, coords):
        # return a list of implicit prop values if pointer need implicit props
        state = self.state
        data = self.state_data

        types = {
            "vertex": (bpy.types.MeshVertex in state.types),
            "edge": (bpy.types.MeshEdge in state.types),
            "face": (bpy.types.MeshPolygon in state.types),
        }

        do_object = bpy.types.Object in state.types
        do_mesh_elem = any(types.values())

        if not do_object and not do_mesh_elem:
            return

        global_ob = None
        if self._has_global_object():
            global_ob_name = self._state_data[self._get_global_object_index()].get("object_name")
            if global_ob_name:
                global_ob = bpy.data.objects[global_ob_name]

        ob, type, index = get_mesh_element(context, coords, **types, object=global_ob)

        if not ob:
            return None

        if bpy.types.Object in state.types:
            data["type"] = bpy.types.Object
            return ob.name

        # maybe have type as return value
        data["type"] = {
            "VERTEX": bpy.types.MeshVertex,
            "EDGE": bpy.types.MeshEdge,
            "FACE": bpy.types.MeshPolygon,
        }[type]

        return ob.name, index


    def get_property(self, index=None):
        if index == None:
            index = self.state_index
        state = self.get_states()[index]

        if state.property == None:
            return None

        if callable(state.property):
            props = state.property(self, index)
        elif state.property:
            if callable(getattr(self, state.property)):
                props = getattr(self, state.property)(index)
            else:
                props = state.property
        elif hasattr(self, "state_property") and callable(self.state_property):
            props = self.state_property(index)
        else:
            return None

        retval = to_list(props)
        return retval


    @classmethod
    def get_states_definition(cls):
        if callable(cls.states):
            return cls.states()
        return cls.states

    def get_states(self):
        if callable(self.states):
            return self.states(operator=self)
        return self.states

    @property
    def state(self):
        return self.get_states()[self.state_index]

    def _index_from_state(self, state):
        return [e.name for e in self.get_states()].index(state)

    @state.setter
    def state(self, state):
        self.state_index = self._index_from_state(state)

    def set_state(self, context, index):
        self.state_index = index
        self.init_numeric(False)
        self.set_status_text(context)

    def next_state(self, context):
        i = self.state_index
        if (i + 1) >= len(self.get_states()):
            return False
        self.set_state(context, i + 1)
        return True

    def set_status_text(self, context):
        # Setup state
        state = self.state
        desc = (
            state.description(self, state)
            if callable(state.description)
            else state.description
        )

        msg = state_desc(state.name, desc, state.types)
        if self.state_data.get("is_numeric_edit", False):
            index = self._substate_index
            prop = self._stateprop
            type = prop.type
            array_length = prop.array_length if prop.array_length else 1
            if type == "FLOAT":
                input = [0.0] * array_length
                for key, val in self._numeric_input.items():
                    input[key] = val

                input[index] = "*" + str(input[index])
                input = str(input).replace('"', "").replace("'", "")
            elif type == "INT":
                input = self.numeric_input

            msg += "    {}: {}".format(prop.subtype, input)

        context.workspace.status_text_set(msg)

    def check_numeric(self):
        """Check if the state supports numeric edit"""

        # TODO: Allow to define custom logic

        state = self.state
        props = self.get_property()

        # Disable for multi props
        if not props or len(props) > 1:
            return False

        prop_name = props[0]
        if not prop_name:
            return False

        prop = self.properties.rna_type.properties[prop_name]
        if not prop.type in ("INT", "FLOAT"):
            return False
        return True

    def init_numeric(self, is_numeric):
        self._numeric_input = {}
        self._substate_index = 0

        ok = False
        if is_numeric:
            ok = self.check_numeric()
            # TODO: not when iterating substates
            self.state_data["is_numeric_edit"] = is_numeric and ok

        self.init_substate()
        return ok

    def init_substate(self):
        props = self.get_property()
        if props and props[0]:
            prop_name = props[0]
            prop = self.properties.rna_type.properties[prop_name]
            self._substate_count = prop.array_length
            self._stateprop = prop
        else:
            self._substate_count = None
            self._stateprop = None

    def iterate_substate(self):
        i = self._substate_index
        if i + 1 >= self._substate_count:
            i = 0
        else:
            i += 1
        self._substate_index = i

    @property
    def numeric_input(self):
        return self._numeric_input.get(self._substate_index, "")

    @numeric_input.setter
    def numeric_input(self, value):
        self._numeric_input[self._substate_index] = value

    def check_event(self, event):
        state = self.state
        if (
            event.type in ("LEFTMOUSE", "RET", "NUMPAD_ENTER")
            and event.value == "PRESS"
        ):
            return True
        if self.state_index == 0 and not self.wait_for_input:
            # Trigger the first state
            return not self.state_data.get("is_numeric_edit", False)
        if state.no_event:
            return True
        return False

    @staticmethod
    def is_numeric_input(event):
        return event.type in (*numeric_events, "BACK_SPACE")

    @staticmethod
    def is_unit_input(event):
        return event.type in (
            "M",
            "K",
            "D",
            "C",
            "U",
            "A",
            "H",
            "I",
            "L",
            "N",
            "F",
            "T",
            "Y",
            "U",
            "R",
            "E",
            "G",
        )

    @staticmethod
    def get_unit_value(event):
        type = event.type
        return type.lower()

    @staticmethod
    def get_value_from_event(event):
        type = event.type
        if type in ("ZERO", "NUMPAD_0"):
            return "0"
        if type in ("ONE", "NUMPAD_1"):
            return "1"
        if type in ("TWO", "NUMPAD_2"):
            return "2"
        if type in ("THREE", "NUMPAD_3"):
            return "3"
        if type in ("FOUR", "NUMPAD_4"):
            return "4"
        if type in ("FIVE", "NUMPAD_5"):
            return "5"
        if type in ("SIX", "NUMPAD_6"):
            return "6"
        if type in ("SEVEN", "NUMPAD_7"):
            return "7"
        if type in ("EIGHT", "NUMPAD_8"):
            return "8"
        if type in ("NINE", "NUMPAD_9"):
            return "9"
        if type in ("PERIOD", "NUMPAD_PERIOD"):
            return "."

    def evaluate_numeric_event(self, event):
        type = event.type
        if type == "BACK_SPACE":
            input = self.numeric_input
            if len(input):
                self.numeric_input = input[:-1]
        elif type in ("MINUS", "NUMPAD_MINUS"):
            input = self.numeric_input
            if input.startswith("-"):
                input = input[1:]
            else:
                input = "-" + input
            self.numeric_input = input
        elif self.is_unit_input(event):
            self.numeric_input += self.get_unit_value(event)
        else:
            self.numeric_input += self.get_value_from_event(event)

    def is_in_previous_states(self, entity):
        i = self.state_index - 1
        while True:
            if i < 0:
                break
            state = self.get_states()[i]
            if state.pointer and entity == getattr(self, state.pointer):
                return True
            i -= 1
        return False

    def gather_selection(self, context):
        # Return list filled with all selected verts/edges/faces/objects
        selected = []
        states = self.get_states()
        types = [s.types for s in states]
        # Note: Where to take mesh elements from? Editmode data is only written
        # when left probably making it impossible to use selected elements in realtime.
        if any([t == bpy.types.Object for t in types]):
            selected.extend(context.selected_objects)

        return selected

    # Gets called for every state
    def parse_selection(self, context, selected, index=None):
        # Look for a valid element in selection
        # should go through objects, vertices, entities depending on state.types

        result = None
        if not index:
            index = self.state_index
        state = self.get_states_definition()[index]
        data = self.get_state_data(index)

        if state.pointer:
            # TODO: Discard if too many entities are selected?
            types = state.types
            for i, e in enumerate(selected):
                if type(e) in types:
                    result = selected.pop(i)
                    break

        if result:
            data["type"] = type(result)
            self.set_state_pointer(to_list(result), index=index)
            self.state_data["is_existing_entity"] = True
            return True


    def prefill_state_props(self, context):
        selected = self.gather_selection(context)
        states = self.get_states_definition()

        # Iterate states and try to prefill state props
        while True:
            index = self.state_index
            result = None
            state = self.state
            data = self.get_state_data(index)
            coords = None

            if not state.allow_prefill:
                break

            func = self.get_func(state, "parse_selection")
            result = func(context, selected, index=index)

            if result:
                if not self.next_state(context):
                    return {"FINISHED"}
                continue
            break
        return {"RUNNING_MODAL"}

    @property
    def state_data(self):
        return self._state_data.setdefault(self.state_index, {})

    def get_state_data(self, index):
        if not self._state_data.get(index):
            self._state_data[index] = {}
        return self._state_data[index]

    def get_func(self, state, name):
        # fallback to operator method if function isn't specified by state
        func = getattr(state, name, None)

        if func:
            if isinstance(func, str):
                # callback can be specified by function name
                return getattr(self, func)
            return func

        if hasattr(self, name):
            return getattr(self, name)
        return None

    def invoke(self, context, event):
        self._state_data.clear()
        if hasattr(self, "init"):
            self.init(context, event)

        retval = {"RUNNING_MODAL"}

        go_modal = True
        if self.is_numeric_input(event):
            if self.init_numeric(True):
                self.evaluate_numeric_event(event)
                retval = {"RUNNING_MODAL"}
                self.evaluate_state(context, event, False)

        # NOTE: This allows to start the operator but waits for action (LMB event).
        # Try to fill states based on selection only when this is True since it doesn't
        # make senese to respect selection when the user interactivley starts the operator.
        elif self.wait_for_input:
            retval = self.prefill_state_props(context)
            if retval == {"FINISHED"}:
                go_modal = False

            # NOTE: It might make sense to cancle Operator if no prop could be filled
            # Otherwise it might not be obvious that an operator is running
            # if self.state_index == 0:
            #     return self._end(context, False)

            if not self.executed and self.check_props():
                self.run_op(context)
                self.executed = True
            context.area.tag_redraw()  # doesnt seem to work...

        self.set_status_text(context)

        if go_modal:
            context.window.cursor_modal_set("CROSSHAIR")
            context.window_manager.modal_handler_add(self)
            return retval

        succeede = retval == {"FINISHED"}
        if succeede:
            # NOTE: It seems like there's no undo step pushed if an operator finishes from invoke
            # could push an undo_step here however this causes duplicated constraints after redo,
            # disable for now
            # bpy.ops.ed.undo_push()
            pass
        return self._end(context, succeede)

    def run_op(self, context):
        if not hasattr(self, "main"):
            raise NotImplementedError(
                "StatefulOperators need to have a main method defined!"
            )
        retval = self.main(context)
        self.executed = True
        return retval

    # Creates non-persistent data
    def redo_states(self, context):
        for i, state in enumerate(self.get_states()):
            if i > self.state_index:
                # TODO: don't depend on active state, idealy it's possible to go back
                break
            if state.pointer:
                data = self._state_data.get(i, {})
                is_existing_entity = data["is_existing_entity"]

                props = self.get_property(index=i)
                if props and not is_existing_entity:
                    create = self.get_func(state, "create_element")

                    ret_values = create(context, [getattr(self, p) for p in props], state, data)
                    values = to_list(ret_values)
                    self.set_state_pointer(values, index=i, implicit=True)

    def execute(self, context):
        self.redo_states(context)
        ok = self.main(context)
        return self._end(context, ok)
        # maybe allow to be modal again?

    def get_numeric_value(self, context, coords):
        state = self.state
        prop_name = self.get_property()[0]
        prop = self.properties.rna_type.properties[prop_name]

        def parse_input(prop, input):
            units = context.scene.unit_settings
            unit = prop.unit
            type = prop.type
            if unit != "NONE":
                try:
                    value = bpy.utils.units.to_value(units.system, unit, input)
                except ValueError:
                    return prop.default
                if type == "INT":
                    value = int(value)
            elif type == "FLOAT":
                value = float(input)
            elif type == "INT":
                value = int(input)
            else:
                value = prop.default
            return value

        size = max(1, self._substate_count)

        def to_iterable(item):
            if hasattr(item, "__iter__") or hasattr(item, "__getitem__"):
                return list(item)
            return [item, ]

        # TODO: Don't evaluate if not needed
        position_cb = self.get_func(state, "state_func")
        interactive_val = to_iterable(position_cb(context, coords))

        storage = [None] * size
        result = [None] * size

        for sub_index in range(size):
            num = None

            input = self._numeric_input.get(sub_index)
            if input:
                num = parse_input(prop, input)
                result[sub_index] = num
                storage[sub_index] = num
            else:
                result[sub_index] = interactive_val[sub_index]

        self.state_data["numeric_input"] = storage

        if not self._substate_count:
            return result[0]
        return result

    def _handle_pass_through(self, context, event):
        # Only pass through navigation events
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', "MOUSEMOVE"}:
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        state = self.state
        event_triggered = self.check_event(event)
        coords = Vector((event.mouse_region_x, event.mouse_region_y))

        is_numeric_edit = self.state_data.get("is_numeric_edit", False)
        is_numeric_event = event.value == "PRESS" and self.is_numeric_input(event)

        if is_numeric_edit:
            if self.is_unit_input(event) and event.value == "PRESS":
                is_numeric_event = True
            elif event.type == "TAB" and event.value == "PRESS":
                self.iterate_substate()
                self.set_status_text(context)
        elif is_numeric_event:
            # Initalize
            is_numeric_edit = self.init_numeric(True)

        if event.type in {"RIGHTMOUSE", "ESC"}:
            return self._end(context, False)

        # HACK: when calling ops.ed.undo() inside an operator a mousemove event
        # is getting triggered. manually check if theres a mousemove...
        mousemove_threshold = 0.1
        is_mousemove = (coords - self._last_coords).length > mousemove_threshold
        self._last_coords = coords

        if not event_triggered:
            if is_numeric_event:
                pass
            elif is_mousemove and is_numeric_edit:
                event_triggered = False
                pass
            elif not state.interactive:
                return self._handle_pass_through(context, event)
            elif not is_mousemove:
                return self._handle_pass_through(context, event)

        # TODO: Disable numeric input when no state.property
        if is_numeric_event:
            self.evaluate_numeric_event(event)
            self.set_status_text(context)

        return self.evaluate_state(context, event, event_triggered)

    def evaluate_state(self, context, event, triggered):
        state = self.state
        data = self.state_data
        is_numeric = self.state_data.get("is_numeric_edit", False)
        coords = Vector((event.mouse_region_x, event.mouse_region_y))

        # Pick hovered element
        hovered = None
        is_picked = False
        if not is_numeric and state.pointer:
            pick = self.get_func(state, "pick_element")
            pick_retval = pick(context, coords)

            if pick_retval != None:
                is_picked = True
                pointer_values = to_list(pick_retval)

        # Set state property
        ok = False
        undo = False
        values = []
        use_create = state.use_create
        if use_create and not is_picked:
            if is_numeric:
                # numeric edit is supported for one property only
                values = [self.get_numeric_value(context, coords), ]
            elif not is_picked:
                position_cb = self.get_func(state, "state_func")
                values = to_list(position_cb(context, coords)) if position_cb else None

            if values:
                props = self.get_property()
                if props:
                    for i, v in enumerate(values):
                        setattr(self, props[i], v)
                    undo = True
                    ok = not state.pointer

        # Set state pointer
        pointer = None
        if state.pointer:
            if is_picked:
                pointer = pointer_values
                self.state_data["is_existing_entity"] = True
                undo = True
            elif values:
                # Let pointer be filled from redo_states
                self.state_data["is_existing_entity"] = False
                ok = True

            if pointer:
                self.set_state_pointer(pointer, implicit=True)
                ok = True

        if undo:
            bpy.ops.ed.undo_push(message="Redo: " + self.bl_label)
            bpy.ops.ed.undo()
            global_data.ignore_list.clear()
            self.redo_states(context)

        if self.check_props():
            self.run_op(context)

        # Iterate state
        if triggered and ok:
            if not self.next_state(context):
                if self.check_continuose_draw():
                    self.do_continuose_draw(context)
                else:
                    return self._end(context, True)

            if is_numeric:
                # NOTE: Run next state already once even if there's no mousemove yet,
                # This is needed in order for the geometry to update
                self.evaluate_state(context, event, False)
        context.area.tag_redraw()


        if triggered and not ok:
            # Event was triggered on non-valid selection, cancel operator to avoid confusion
            return self._end(context, False)

        if triggered or is_numeric:
            return {"RUNNING_MODAL"}
        return self._handle_pass_through(context, event)

    def check_continuose_draw(self):
        if self.continuose_draw:
            if not hasattr(self, "continue_draw") or self.continue_draw():
                return True
        return False

    def _reset_op(self):
        self.executed = False
        for i, s in enumerate(self.get_states()):
            if not s.pointer:
                continue
            self.set_state_pointer(None, index=i)
        self._state_data.clear()

    def do_continuose_draw(self, context):
        # end operator
        self._end(context, True)
        bpy.ops.ed.undo_push(message=self.bl_label)

        # save last prop
        last_pointer = None
        for i, s in reversed(list(enumerate(self.get_states()))):
            if not s.pointer:
                continue
            last_index = i
            last_pointer = getattr(self, s.pointer)
            break

        values = to_list(self.get_state_pointer(index=last_index, implicit=True))

        # reset operator
        self._reset_op()

        data = {}
        self._state_data[0] = data
        data["is_existing_entity"] = True
        data["type"] = type(last_pointer)

        # set first pointer
        self.set_state_pointer(values, index=0, implicit=True)
        self.set_state(context, 1)

    def _end(self, context, succeede):
        context.window.cursor_modal_restore()
        if hasattr(self, "fini"):
            self.fini(context, succeede)
        global_data.ignore_list.clear()

        context.workspace.status_text_set(None)
        if succeede:
            return {"FINISHED"}
        else:
            bpy.ops.ed.undo_push(message="Cancelled: " + self.bl_label)
            bpy.ops.ed.undo()
            return {"CANCELLED"}

    def check_props(self):
        for i, state in enumerate(self.get_states()):
            props = self.get_property(index=i)
            if state.pointer:
                if not bool(self.get_state_pointer(index=i)):
                    return False

            elif props:
                for p in props:
                    if not self.properties.is_property_set(p):
                        return False
        return True

    def draw(self, context):
        layout = self.layout

        for i, state in enumerate(self.get_states()):
            if i != 0:
                layout.separator()

            layout.label(text=state.name)

            state_data = self._state_data.get(i, {})
            is_existing = state_data.get("is_existing_entity", False)
            props = self.get_property(index=i)

            if state.pointer and is_existing:
                layout.label(text=str(getattr(self, state.pointer)))
            elif props:
                for p in props:
                    layout.prop(self, p, text="")


# StatefulOperator Doc

# Operator Methods
# main(self, context) -> succeede(bool)
#   function which creates the actual result of the operator,
#   e.g. the main function of an add_line operator creates the line

# register_properties(cls) -> None
#   can be used to store implicit pointer properties and pointer fallback properties

# get_state_pointer(self, index=None) -> result(ANY)
#   method for pointer access, either of active state or by state index

# set_state_pointer(self, value, index=None) -> succeede(bool)
#   method to set state pointer, either of active state or by state index

# check_props(self) -> succeede(bool)
#   additional poll function to check if all neccesary operator properties
#   are set and the main function can be called

# init(self, context, event) -> None

# fini(self, context, succeede) -> None

# check_pointer(self, prop_name) -> is_set(bool)
#   check if a state pointer is set

# gather_selection(self, context) -> selected(list(ANY))
#   gather the currently selected elements that are later used to fill state pointers with

# State Definition
# state_func(self, context, coords) property_value(ANY)
#   method to get the value for the state property from mouse coordinates

# pick_element(self, context, coords) -> element or it's implicit props
#   method to pick a matching element from mouse coordinates, either return the
#   element or it's implicit prop values, has to set the type of the picked element

# create_element(self, context, value, state, state_data) -> element or it's implicit props
#   method to create state element when no existing element gets picked,
#   has to set the type of the created element


from collections import namedtuple

OperatorState = namedtuple(
    "OperatorState",
    (
        "name",  # The name to display in the interface
        "description",  # Text to be displayed in statusbar
        # Operator property this state acts upon
        # Can also be a list of property names or a callback that returns
        # a set of properties dynamicly. When not explicitly set to None the
        # operators state_property function will be called.
        "property",
        # Optional: A state can reference an element, pointer attribute set the name of property function
        # if set this will be passed to main func,
        # state_func should fill main property and create_element should fill this property
        # maybe this could just store it in a normal attr, should work as long as the same operator instance is used, test!
        "pointer",
        "types",  # Types the pointer property can accept
        "no_event",  # Trigger state without an event
        "interactive",  # Always evaluate state and confirm by user input
        "use_create", # Enables or Disables creation of the element
        "state_func",  # Function to get the state property value from mouse coordinates
        "allow_prefill",  # Define if state should be filled from selected entities when invoked
        "parse_selection",  # Prefill Function which chooses entity to use for this stat
        "pick_element",
        "create_element",
        # TODO: Implement!
        "use_interactive_placemenet",  # Create new state element based on mouse coordinates
        "check_pointer",
    ),
)
del namedtuple


def state_from_args(name, **kwargs):
    """
    Use so each state can avoid defining all members of the named tuple.
    """
    kw = {
        "name": name,
        "description": "",
        "property": "",
        "pointer": None,
        "types": (),
        "no_event": False,
        "interactive": False,
        "use_create" : True,
        "state_func": None,
        "allow_prefill": True,
        "parse_selection": None,
        "pick_element": None,
        "create_element": None,
        "use_interactive_placemenet": True,
        "check_pointer": None,
    }
    kw.update(kwargs)
    return OperatorState(**kw)


from bpy_extras.view3d_utils import region_2d_to_location_3d, region_2d_to_vector_3d


class GenericEntityOp(StatefulOperator):
    def pick_element(self, context, coords):
        retval = super().pick_element(context, coords)
        if retval != None:
            return retval

        state = self.state
        data = self.state_data

        hovered = get_hovered(context, *state.types)
        if hovered and self.is_in_previous_states(hovered):
            hovered = None

        # Set the hovered entity for constraining if not directly used
        hovered_index = -1
        if not hovered and hasattr(self, "_check_constrain"):
            hover = global_data.hover
            if hover and self._check_constrain(context, hover):
                hovered_index = hover

        data["hovered"] = hovered_index
        data["type"] = type(hovered) if hovered else None
        return hovered.slvs_index if hovered else None

    def add_coincident(self, context, point, state, state_data):
        index = state_data.get("hovered", -1)
        if index != -1:
            hovered = context.scene.sketcher.entities.get(index)
            constraints = context.scene.sketcher.constraints

            sketch = None
            if hasattr(self, "sketch"):
                sketch = self.sketch
            state_data["coincident"] = constraints.add_coincident(
                point, hovered, sketch=sketch
            )

    def has_coincident(self):
        for state_index, data in self._state_data.items():
            if data.get("coincident", None):
                return True
        return False

    @classmethod
    def register_properties(cls):
        super().register_properties()

        states = cls.get_states_definition()

        for s in states:
            if not s.pointer:
                continue

            name = s.pointer
            types = s.types

            annotations = {}
            if hasattr(cls, "__annotations__"):
                annotations = cls.__annotations__.copy()


            # handle SlvsPoint3D fallback props
            if any([t == class_defines.SlvsPoint3D for t in types]):
                kwargs = {"size": 3, "subtype": "XYZ", "unit": "LENGTH"}
                annotations[name + "_fallback"] = FloatVectorProperty(name=name, **kwargs)

            # handle SlvsPoint2D fallback props
            if any([t == class_defines.SlvsPoint2D for t in types]):
                kwargs = {"size": 2, "subtype": "XYZ", "unit": "LENGTH"}
                annotations[name + "_fallback"] = FloatVectorProperty(name=name, **kwargs)

            if any([t == class_defines.SlvsNormal3D for t in types]):
                kwargs = {"size": 3, "subtype": "EULER", "unit": "ROTATION"}
                annotations[name + "_fallback"] = FloatVectorProperty(name=name, **kwargs)

            for a in annotations.keys():
                if hasattr(cls, a):
                    raise NameError("Class {} already has attribute of name {}, cannot register implicit pointer properties".format(cls, a))
            setattr(cls, "__annotations__", annotations)


    def state_property(self, state_index):
        # Return state_prop / properties. Handle multiple types
        props = super().state_property(state_index)
        if props:
            return props

        state = self.get_states_definition()[state_index]

        pointer_name = state.pointer
        if not pointer_name:
            return ""

        if any([issubclass(t, class_defines.SlvsGenericEntity) for t in state.types]):
            return pointer_name + "_fallback"
        return ""

    def get_state_pointer(self, index=None, implicit=False):
        retval = super().get_state_pointer(index=index, implicit=implicit)
        if retval:
            return retval

        # Creates pointer from it's implicitly stored props
        if index is None:
            index = self.state_index

        state = self.get_states_definition()[index]
        pointer_name = state.pointer
        data = self._state_data.get(index, {})
        if not "type" in data.keys():
            return None

        pointer_type = data["type"]
        if not pointer_type:
            return None

        if issubclass(pointer_type, class_defines.SlvsGenericEntity):
            i = data["entity_index"]
            if implicit:
                return i

            if i == -1:
                return None
            return bpy.context.scene.sketcher.entities.get(i)


    def set_state_pointer(self, values, index=None, implicit=False):
        retval = super().set_state_pointer(values, index=index, implicit=implicit)
        if retval:
            return retval

        # handles type specific setters
        if index is None:
            index = self.state_index

        state = self.get_states_definition()[index]
        pointer_name = state.pointer
        data = self._state_data.get(index, {})
        pointer_type = data["type"]

        if issubclass(pointer_type, class_defines.SlvsGenericEntity):
            value = values[0] if values != None else None

            if value == None:
                i = -1
            elif implicit:
                i = value
            else:
                i = value.slvs_index
            data["entity_index"] = i
            return True

    def gather_selection(self, context):
        # Return list filled with all selected verts/edges/faces/objects
        selected = super().gather_selection(context)
        states = self.get_states()
        types = [s.types for s in states]

        selected.extend(list(context.scene.sketcher.entities.selected_entities))
        return selected


class Operator3d(GenericEntityOp):
    @classmethod
    def poll(cls, context):
        return context.scene.sketcher.active_sketch_i == -1

    def init(self, context, event):
        pass

    def state_func(self, context, coords):
        return functions.get_placement_pos(context, coords)

    def create_element(self, context, values, state, state_data):
        sse = context.scene.sketcher.entities
        loc = values[0]
        point = sse.add_point_3d(loc)
        self.add_coincident(context, point, state, state_data)

        ignore_hover(point)
        state_data["type"] = type(point)
        return point.slvs_index

    # Check if hovered entity should be constrained
    def _check_constrain(self, context, index):
        type = context.scene.sketcher.entities.type_from_index(index)
        return type in (class_defines.SlvsLine3D, class_defines.SlvsWorkplane)

    def get_point(self, context, index):
        states = self.get_states_definition()
        state = states[index]
        data = self._state_data[index]
        type = data["type"]
        sse = context.scene.sketcher.entities

        if type == bpy.types.MeshVertex:
            ob_name, v_index = self.get_state_pointer(index=index, implicit=True)
            ob = bpy.data.objects[ob_name]
            return sse.add_ref_vertex_3d(ob, v_index)
        return getattr(self, state.pointer)


class Operator2d(GenericEntityOp):
    @classmethod
    def poll(cls, context):
        return context.scene.sketcher.active_sketch_i != -1

    def init(self, context, event):
        self.sketch = context.scene.sketcher.active_sketch

    def state_func(self, context, coords):
        wp = self.sketch.wp
        origin, end_point = functions.get_picking_origin_end(context, coords)
        pos = intersect_line_plane(origin, end_point, wp.p1.location, wp.normal)
        pos = wp.matrix_basis.inverted() @ pos
        return Vector(pos[:-1])


    # create element depending on mode
    def create_element(self, context, values, state, state_data):
        sse = context.scene.sketcher.entities
        sketch = self.sketch
        loc = values[0]
        point = sse.add_point_2d(loc, sketch)
        self.add_coincident(context, point, state, state_data)

        ignore_hover(point)
        state_data["type"] = type(point)
        return point.slvs_index

    def _check_constrain(self, context, index):
        type = context.scene.sketcher.entities.type_from_index(index)
        return type in (
            class_defines.SlvsLine2D,
            class_defines.SlvsCircle,
            class_defines.SlvsArc,
        )

    def get_point(self, context, index):
        states = self.get_states_definition()
        state = states[index]
        data = self._state_data[index]
        type = data["type"]
        sse = context.scene.sketcher.entities
        sketch = self.sketch

        if type == bpy.types.MeshVertex:
            ob_name, v_index = self.get_state_pointer(index=index, implicit=True)
            ob = bpy.data.objects[ob_name]
            return sse.add_ref_vertex_2d(ob, v_index, sketch)
        return getattr(self, state.pointer)

class_defines.slvs_entity_pointer(Operator2d, "sketch")

p3d_state1_doc = ("Location", "Set point's location.")


class View3D_OT_slvs_add_point3d(Operator, Operator3d):
    bl_idname = "view3d.slvs_add_point3d"
    bl_label = "Add Solvespace 3D Point"
    bl_options = {"REGISTER", "UNDO"}

    location: FloatVectorProperty(name="Location", subtype="XYZ")

    states = (
        state_from_args(
            p3d_state1_doc[0],
            description=p3d_state1_doc[1],
            property="location",
        ),
    )

    __doc__ = stateful_op_desc(
        "Add a point in 3d space",
        state_desc(*p3d_state1_doc, None),
    )

    def main(self, context):
        self.target = context.scene.sketcher.entities.add_point_3d(self.location)

        # Store hovered entity to use for auto-coincident since it doesnt get
        # stored for non-interactive tools
        hovered = global_data.hover
        if self._check_constrain(context, hovered):
            self.state_data["hovered"] = hovered

        self.add_coincident(context, self.target, self.state, self.state_data)
        return True

    def fini(self, context, succeede):
        if hasattr(self, "target"):
            logger.debug("Add: {}".format(self.target))


types_point_3d = (
    *class_defines.point_3d,
    *((bpy.types.MeshVertex,) if False else ()),
)

l3d_state1_doc = ("Startpoint", "Pick or place line's starting point.")
l3d_state2_doc = ("Endpoint", "Pick or place line's ending point.")


class View3D_OT_slvs_add_line3d(Operator, Operator3d):
    bl_idname = "view3d.slvs_add_line3d"
    bl_label = "Add Solvespace 3D Line"
    bl_options = {"REGISTER", "UNDO"}

    continuose_draw: BoolProperty(name="Continuose Draw", default=True)

    states = (
        state_from_args(
            l3d_state1_doc[0],
            description=l3d_state1_doc[1],
            pointer="p1",
            types=types_point_3d,
        ),
        state_from_args(
            l3d_state2_doc[0],
            description=l3d_state2_doc[1],
            pointer="p2",
            types=types_point_3d,
            interactive=True,
        ),
    )

    __doc__ = stateful_op_desc(
        "Add a line in 3d space",
        state_desc(*l3d_state1_doc, types_point_3d),
        state_desc(*l3d_state2_doc, types_point_3d),
    )

    def main(self, context):
        p1, p2 = self.get_point(context, 0), self.get_point(context, 1)

        self.target = context.scene.sketcher.entities.add_line_3d(p1, p2)
        ignore_hover(self.target)
        return True

    def continue_draw(self):
        last_state = self._state_data[1]
        if last_state["is_existing_entity"]:
            return False

        # also not when last state has coincident constraint
        if last_state.get("coincident"):
            return False
        return True

    def fini(self, context, succeede):
        if hasattr(self, "target"):
            logger.debug("Add: {}".format(self.target))

        if succeede:
            if self.has_coincident:
                solve_system(context)


wp_state1_doc = ("Origin", "Pick or place workplanes's origin.")
wp_state2_doc = ("Orientation", "Set workplane's orientation.")


class View3D_OT_slvs_add_workplane(Operator, Operator3d):
    bl_idname = "view3d.slvs_add_workplane"
    bl_label = "Add Solvespace Workplane"
    bl_options = {"REGISTER", "UNDO"}

    states = (
        state_from_args(
            wp_state1_doc[0],
            description=wp_state1_doc[1],
            pointer="p1",
            types=types_point_3d,
        ),
        state_from_args(
            wp_state2_doc[0],
            description=wp_state2_doc[1],
            state_func="get_orientation",
            pointer="nm",
            types=(*class_defines.normal_3d, bpy.types.MeshPolygon),
            interactive=True,
            create_element="create_normal3d",
        ),
    )


    __doc__ = stateful_op_desc(
        "Add a workplane",
        state_desc(*wp_state1_doc, types_point_3d),
        state_desc(*wp_state2_doc, None),
    )

    def get_normal(self, context, index):
        states = self.get_states_definition()
        state = states[index]
        data = self._state_data[index]
        type = data["type"]
        sse = context.scene.sketcher.entities

        if type == bpy.types.MeshPolygon:
            ob_name, nm_index = self.get_state_pointer(index=index, implicit=True)
            ob = bpy.data.objects[ob_name]
            return sse.add_ref_normal_3d(ob, nm_index)
        return getattr(self, state.pointer)

    def get_orientation(self, context, coords):
        # TODO: also support edges
        data = self.state_data
        ob, type, index = get_mesh_element(context, coords, edge=False, face=True)

        p1 = self.get_point(context, 0)
        mousepos = functions.get_placement_pos(context, coords)
        vec = mousepos - p1.location
        return global_data.Z_AXIS.rotation_difference(vec).to_euler()

    def create_normal3d(self, context, values, state, state_data):
        sse = context.scene.sketcher.entities

        v = values[0].to_quaternion()
        nm = sse.add_normal_3d(v)
        state_data["type"] = class_defines.SlvsNormal3D
        return nm.slvs_index

    def main(self, context):
        sse = context.scene.sketcher.entities
        p1 = self.get_point(context, 0)
        nm = self.get_normal(context, 1)
        self.target = sse.add_workplane(p1, nm)
        ignore_hover(self.target)
        return True

    def fini(self, context, succeede):
        if hasattr(self, "target"):
            logger.debug("Add: {}".format(self.target))

        if succeede:
            if self.has_coincident:
                solve_system(context)


wp_face_state1_doc = ("Face", "Pick a mesh face to use as workplanes's transformation.")


class View3D_OT_slvs_add_workplane_face(Operator, Operator3d):
    bl_idname = "view3d.slvs_add_workplane_face"
    bl_label = "Add Solvespace Workplane"
    bl_options = {"REGISTER", "UNDO"}


    states = (
        state_from_args(
            wp_face_state1_doc[0],
            description=wp_face_state1_doc[1],
            use_create=False,
            pointer="face",
            types=(bpy.types.MeshPolygon, ),
            interactive=True,
        ),
    )

    __doc__ = stateful_op_desc(
        "Add a statically placed workplane, orientation and location is copied from selected mesh face",
        state_desc(*wp_face_state1_doc, types_point_3d),
    )

    def main(self, context):
        sse = context.scene.sketcher.entities

        ob_name, face_index = self.get_state_pointer(index=0, implicit=True)
        ob = get_evaluated_obj(context, bpy.data.objects[ob_name])
        mesh = ob.data
        face = mesh.polygons[face_index]

        quat = class_defines.get_face_orientation(mesh, face)
        # pos = class_defines.get_face_midpoint(quat, ob, face)
        pos = face.center
        origin = sse.add_point_3d(pos)
        nm = sse.add_normal_3d(quat)

        self.target = sse.add_workplane(origin, nm)
        ignore_hover(self.target)
        return True



from . import gizmos

sketch_state1_doc = ["Workplane", "Pick a workplane as base for the sketch."]

# TODO:
# - Draw sketches
class View3D_OT_slvs_add_sketch(Operator, Operator3d):
    bl_idname = "view3d.slvs_add_sketch"
    bl_label = "Add Sketch"
    bl_options = {"UNDO"}


    states = (
        state_from_args(
            sketch_state1_doc[0],
            description=sketch_state1_doc[1],
            pointer="wp",
            types=(class_defines.SlvsWorkplane,),
            property=None,
        ),
    )

    __doc__ = stateful_op_desc(
        "Add a sketch",
        state_desc(*sketch_state1_doc, (class_defines.SlvsWorkplane,)),
    )

    def ensure_preselect_gizmo(self, context, _coords):
        tool = context.workspace.tools.from_space_view3d_mode(context.mode)
        if tool.widget != gizmos.VIEW3D_GGT_slvs_preselection.bl_idname:
            bpy.ops.wm.tool_set_by_id(name="sketcher.slvs_select")
        return True

    def prepare_origin_elements(self, context, _coords):
        context.scene.sketcher.entities.ensure_origin_elements(context)
        return True

    def init(self, context, event):
        self.ensure_preselect_gizmo(context, None)
        self.prepare_origin_elements(context, None)
        bpy.ops.ed.undo_push(message="Ensure Origin Elements")
        context.scene.sketcher.show_origin = True

    def main(self, context):
        sse = context.scene.sketcher.entities
        sketch = sse.add_sketch(self.wp)

        # Add point at origin
        # NOTE: Maybe this could create a refrence entity of the main origin?
        p = sse.add_point_2d((0.0, 0.0), sketch)
        p.fixed = True

        context.scene.sketcher.active_sketch = sketch
        self.target = sketch
        return True

    def fini(self, context, succeede):
        context.scene.sketcher.show_origin = False
        if hasattr(self, "target"):
            logger.debug("Add: {}".format(self.target))

        if succeede:
            self.wp.visible = False


p2d_state1_doc = ("Coordinates", "Set point's coordinates on the sketch.")


class View3D_OT_slvs_add_point2d(Operator, Operator2d):
    bl_idname = "view3d.slvs_add_point2d"
    bl_label = "Add Solvespace 2D Point"
    bl_options = {"REGISTER", "UNDO"}

    coordinates: FloatVectorProperty(name="Coordinates", size=2)

    states = (
        state_from_args(
            p2d_state1_doc[0],
            description=p2d_state1_doc[1],
            property="coordinates",
        ),
    )

    __doc__ = stateful_op_desc(
        "Add a point to the active sketch",
        state_desc(*p2d_state1_doc, None),
    )

    def main(self, context):
        sketch = self.sketch
        self.target = context.scene.sketcher.entities.add_point_2d(
            self.coordinates, sketch
        )

        # Store hovered entity to use for auto-coincident since it doesnt get
        # stored for non-interactive tools
        hovered = global_data.hover
        if self._check_constrain(context, hovered):
            self.state_data["hovered"] = hovered

        self.add_coincident(context, self.target, self.state, self.state_data)
        return True

    def fini(self, context, succeede):
        if hasattr(self, "target"):
            logger.debug("Add: {}".format(self.target))

        if succeede:
            if self.has_coincident:
                solve_system(context, sketch=self.sketch)

types_point_2d = (
    *class_defines.point_2d,
    *((bpy.types.MeshVertex,) if False else ()),
)


l2d_state1_doc = ("Startpoint", "Pick or place line's starting Point.")
l2d_state2_doc = ("Endpoint", "Pick or place line's ending Point.")


class View3D_OT_slvs_add_line2d(Operator, Operator2d):
    bl_idname = "view3d.slvs_add_line2d"
    bl_label = "Add Solvespace 2D Line"
    bl_options = {"REGISTER", "UNDO"}

    continuose_draw: BoolProperty(name="Continuose Draw", default=True)

    states = (
        state_from_args(
            l2d_state1_doc[0],
            description=l2d_state1_doc[1],
            pointer="p1",
            types=types_point_2d,
        ),
        state_from_args(
            l2d_state2_doc[0],
            description=l2d_state2_doc[1],
            pointer="p2",
            types=types_point_2d,
            interactive=True,
        ),
    )

    __doc__ = stateful_op_desc(
        "Add a line to the active sketch",
        state_desc(*l2d_state1_doc, types_point_2d),
        state_desc(*l2d_state2_doc, types_point_2d),
    )

    def main(self, context):
        wp = self.sketch.wp
        p1, p2 = self.get_point(context, 0), self.get_point(context, 1)

        self.target = context.scene.sketcher.entities.add_line_2d(
            p1, p2, self.sketch
        )

        # auto vertical/horizontal constraint
        constraints = context.scene.sketcher.constraints
        vec_dir = self.target.direction_vec()
        if vec_dir.length:
            angle = vec_dir.angle(Vector((1, 0)))

            threshold = 0.1
            if angle < threshold or angle > math.pi - threshold:
                constraints.add_horizontal(self.target, sketch=self.sketch)
            elif (math.pi / 2 - threshold) < angle < (math.pi / 2 + threshold):
                constraints.add_vertical(self.target, sketch=self.sketch)

        ignore_hover(self.target)
        return True

    def continue_draw(self):
        last_state = self._state_data[1]
        if last_state["is_existing_entity"]:
            return False

        # also not when last state has coincident constraint
        if last_state.get("coincident"):
            return False
        return True

    def fini(self, context, succeede):
        if hasattr(self, "target"):
            logger.debug("Add: {}".format(self.target))

        if succeede:
            if self.has_coincident:
                solve_system(context, sketch=self.sketch)


circle_state1_doc = ("Center", "Pick or place circle's center point.")
circle_state2_doc = ("Radius", "Set circle's radius.")


class View3D_OT_slvs_add_circle2d(Operator, Operator2d):
    bl_idname = "view3d.slvs_add_circle2d"
    bl_label = "Add Solvespace 2D Circle"
    bl_options = {"REGISTER", "UNDO"}

    radius: FloatProperty(name="Radius", subtype="DISTANCE", unit="LENGTH")

    states = (
        state_from_args(
            circle_state1_doc[0],
            description=circle_state1_doc[1],
            pointer="ct",
            types=types_point_2d,
        ),
        state_from_args(
            circle_state2_doc[0],
            description=circle_state2_doc[1],
            property="radius",
            state_func="get_radius",
            interactive=True,
            allow_prefill=False,
        ),
    )

    __doc__ = stateful_op_desc(
        "Add a circle to the active sketch",
        state_desc(*circle_state1_doc, types_point_2d),
        state_desc(*circle_state2_doc, None),
    )

    def get_radius(self, context, coords):
        wp = self.sketch.wp
        pos = self.state_func(context, coords)

        delta = Vector(pos) - self.ct.co
        radius = delta.length
        return radius

    def main(self, context):
        wp = self.sketch.wp
        ct = self.get_point(context, 0)
        self.target = context.scene.sketcher.entities.add_circle(
            wp.nm, ct, self.radius, self.sketch
        )
        ignore_hover(self.target)
        return True

    def fini(self, context, succeede):
        if hasattr(self, "target"):
            logger.debug("Add: {}".format(self.target))

        if succeede:
            if self.has_coincident:
                solve_system(context, sketch=self.sketch)


arc_state1_doc = ("Center", "Pick or place center point.")
arc_state2_doc = ("Startpoint", "Pick or place starting point.")
arc_state3_doc = ("Endpoint", "Pick or place ending point.")


class View3D_OT_slvs_add_arc2d(Operator, Operator2d):
    bl_idname = "view3d.slvs_add_arc2d"
    bl_label = "Add Solvespace 2D Arc"
    bl_options = {"REGISTER", "UNDO"}

    states = (
        state_from_args(
            arc_state1_doc[0],
            description=arc_state1_doc[1],
            pointer="ct",
            types=types_point_2d,
        ),
        state_from_args(
            arc_state2_doc[0],
            description=arc_state2_doc[1],
            pointer="p1",
            types=types_point_2d,
            allow_prefill=False,
        ),
        state_from_args(
            arc_state3_doc[0],
            description=arc_state3_doc[1],
            pointer="p2",
            types=types_point_2d,
            state_func="get_endpoint_pos",
            interactive=True,
        ),
    )

    __doc__ = stateful_op_desc(
        "Add an arc to the active sketch",
        state_desc(*arc_state1_doc, types_point_2d),
        state_desc(*arc_state2_doc, types_point_2d),
        state_desc(*arc_state3_doc, types_point_2d),
    )

    def get_endpoint_pos(self, context, coords):
        mouse_pos = self.state_func(context, coords)

        # Get angle to mouse pos
        ct = self.get_point(context, 0).co
        x, y = Vector(mouse_pos) - ct
        angle = math.atan2(y, x)

        # Get radius from distance ct - p1
        p1 = self.get_point(context, 1).co
        radius = (p1 - ct).length
        pos = functions.pol2cart(radius, angle) + ct
        return pos

    def solve_state(self, context, _event):
        sketch = context.scene.sketcher.active_sketch
        solve_system(context, sketch=sketch)
        return True

    def main(self, context):
        ct, p1, p2 = self.get_point(context, 0), self.get_point(context, 1), self.get_point(context, 2)
        sketch = self.sketch
        sse = context.scene.sketcher.entities
        arc = sse.add_arc(sketch.wp.nm, ct, p1, p2, sketch)

        center = ct.co
        start = p1.co - center
        end = p2.co - center
        a = end.angle_signed(start)
        arc.invert_direction = a < 0

        ignore_hover(arc)
        self.target = arc
        return True

    def fini(self, context, succeede):
        if hasattr(self, "target"):
            logger.debug("Add: {}".format(self.target))
            self.solve_state(context, self.sketch)


rect_state1_doc = ("Startpoint", "Pick or place starting point.")
rect_state2_doc = ("Endpoint", "Pick or place ending point.")


class View3D_OT_slvs_add_rectangle(Operator, Operator2d):
    bl_idname = "view3d.slvs_add_rectangle"
    bl_label = "Add Rectangle"
    bl_options = {"REGISTER", "UNDO"}

    states = (
        state_from_args(
            rect_state1_doc[0],
            description=rect_state1_doc[1],
            pointer="p1",
            types=types_point_2d,
        ),
        state_from_args(
            rect_state2_doc[0],
            description=rect_state2_doc[1],
            pointer="p2",
            types=types_point_2d,
            interactive=True,
            create_element="create_point",
        ),
    )

    __doc__ = stateful_op_desc(
        "Add a rectangle to the active sketch",
        state_desc(*rect_state1_doc, types_point_2d),
        state_desc(*rect_state1_doc, types_point_2d),
    )

    def main(self, context):
        sketch = self.sketch
        sse = context.scene.sketcher.entities

        p1, p2 = self.get_point(context, 0), self.get_point(context, 1)
        p_lb, p_rt = p1, p2

        p_rb = sse.add_point_2d((p_rt.co.x, p_lb.co.y), sketch)
        p_lt = sse.add_point_2d((p_lb.co.x, p_rt.co.y), sketch)

        lines = []
        points = (p_lb, p_rb, p_rt, p_lt)
        for i, start in enumerate(points):
            end = points[i + 1 if i < len(points) - 1 else 0]

            l = sse.add_line_2d(start, end, sketch)
            lines.append(l)

        self.lines = lines

        for e in (*points, *lines):
            ignore_hover(e)
        return True

    def fini(self, context, succeede):
        if hasattr(self, "lines") and self.lines:
            ssc = context.scene.sketcher.constraints
            for i, line in enumerate(self.lines):
                func = ssc.add_horizontal if (i % 2) == 0 else ssc.add_vertical
                func(line, sketch=self.sketch)

            data = self._state_data.get(1)
            if data.get("is_numeric_edit", False):
                input = data.get("numeric_input")

                # constrain distance
                startpoint = getattr(self, self.get_states()[0].pointer)
                for val, line in zip(input, (self.lines[1], self.lines[2])):
                    if val == None:
                        continue
                    ssc.add_distance(
                        startpoint,
                        line,
                        sketch=self.sketch,
                        init=True,
                    )

        if succeede:
            if self.has_coincident:
                solve_system(context, sketch=self.sketch)

    def create_point(self, context, values, state, state_data):
        value = values[0]

        if state_data.get("is_numeric_edit", False):
            data = self._state_data.get(1)
            input = data.get("numeric_input")
            # use relative coordinates
            orig = getattr(self, self.get_states()[0].pointer).co

            for i, val in enumerate(input):
                if val == None:
                    continue
                value[i] = orig[i] + val

        sse = context.scene.sketcher.entities
        point = sse.add_point_2d(value, self.sketch)
        ignore_hover(point)

        self.add_coincident(context, point, state, state_data)
        state_data["type"] = class_defines.SlvsPoint2D
        return point.slvs_index


class View3D_OT_slvs_test(Operator, GenericEntityOp):
    bl_idname = "view3d.slvs_test"
    bl_label = "Test StateOps"
    bl_options = {"REGISTER", "UNDO"}


    states = (
        state_from_args(
            "ob",
            pointer="object",
            types=(bpy.types.Object,),
        ),
        state_from_args(
            "Pick Element",
            description="Pick an element to print",
            pointer="element",
            types=(
                *class_defines.point,
                *class_defines.line,
                *class_defines.curve,
                bpy.types.MeshVertex,
                bpy.types.MeshEdge,
                bpy.types.MeshPolygon,

            ),
        ),
    )

    def main(self, context):
        element = self.element
        if element:
            self.report({"INFO"}, "Picked element " + str(element))
            return True
        return False


class View3D_OT_invoke_tool(Operator):
    bl_idname = "view3d.invoke_tool"
    bl_label = "Invoke Tool"

    tool_name: StringProperty(name="Tool ID")

    # TODO: get the operator from tool attribute (tool.bl_operator)?
    operator: StringProperty(name="Operator ID")

    def execute(self, context):
        bpy.ops.wm.tool_set_by_id(name=self.tool_name)

        # get the tool operator props
        tool = context.workspace.tools.from_space_view3d_mode(context.mode)
        props = tool.operator_properties(self.operator)

        options = {}
        for p in props.rna_type.properties.keys():
            if p in ("bl_rna", "rna_type", "state_index"):
                continue
            if p.startswith("_"):
                continue

            default = props.rna_type.properties[p].default
            value = getattr(props, p)

            # NOTE: Setting all values might mess around with operators that check
            # if properties are set. Checking is_property_set here doesn't seem to work.
            # manually check if value is the same as the default however that also
            # seems to fail for vectorproperties and maybe others but prevents
            # the problems from caused by pointer set-state checking...
            if value != default:
                options[p] = value

        options["wait_for_input"] = True

        op_name = self.operator.split(".", 1)
        op = getattr(getattr(bpy.ops, op_name[0]), op_name[1])
        if op.poll():
            op("INVOKE_DEFAULT", **options)
        return {"FINISHED"}


def activate_sketch(context, index, operator):
    props = context.scene.sketcher

    if index == props.active_sketch_i:
        return {"CANCELLED"}

    space_data = context.space_data

    sk = None
    if index != -1:
        sk = context.scene.sketcher.entities.get(index)
        if not sk:
            operator.report({"ERROR"}, "Invalid index: {}".format(index))
            return {"CANCELLED"}

        space_data.show_object_viewport_curve = False
        space_data.show_object_viewport_mesh = False
    else:
        space_data.show_object_viewport_curve = True
        space_data.show_object_viewport_mesh = True

    logger.debug("Activate: {}".format(sk))
    props.active_sketch_i = index

    update_convertor_geometry(context.scene)
    context.area.tag_redraw()
    return {"FINISHED"}


class View3D_OT_slvs_set_active_sketch(Operator):
    """Set the active sketch"""

    bl_idname = "view3d.slvs_set_active_sketch"
    bl_label = "Set active Sketch"
    bl_options = {"UNDO"}

    index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        return activate_sketch(context, self.index, self)


def flatten_deps(entity):
    """Return flattened list of entities given entity depends on"""
    list = []

    def walker(entity, is_root=False):
        if entity in list:
            return
        if not is_root:
            list.append(entity)
        if not hasattr(entity, "dependencies"):
            return
        for e in entity.dependencies():
            if e in list:
                continue
            walker(e)

    walker(entity, is_root=True)
    return list


def is_referenced(entity, context):
    """Check if entity is a dependency of another entity"""
    for e in context.scene.sketcher.entities.all:
        if entity in flatten_deps(e):
            return True
    return False


def get_sketch_deps_indecies(sketch, context):
    deps = []
    for e in context.scene.sketcher.entities.all:
        if not hasattr(e, "sketch_i"):
            continue
        if sketch.slvs_index != e.sketch.slvs_index:
            continue
        deps.append(e.slvs_index)
    return deps


def get_constraint_local_indices(entity, context):
    constraints = context.scene.sketcher.constraints
    ret_list = []

    for data_coll in constraints.get_lists():
        indices = []
        for c in data_coll:
            if entity in c.dependencies():
                indices.append(constraints.get_index(c))
        ret_list.append((data_coll, indices))
    return ret_list


class View3D_OT_slvs_delete_entity(Operator, HighlightElement):
    """Delete Entity by index or based on the selection if index isn't provided
    """
    bl_idname = "view3d.slvs_delete_entity"
    bl_label = "Delete Solvespace Entity"
    bl_options = {"UNDO"}
    bl_description = (
        "Delete Entity by index or based on the selection if index isn't provided"
    )

    index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return True

    @staticmethod
    def main(context, index, operator):
        entities = context.scene.sketcher.entities
        entity = entities.get(index)

        if not entity:
            return {"CANCELLED"}

        if isinstance(entity, class_defines.SlvsSketch):
            if context.scene.sketcher.active_sketch_i != -1:
                activate_sketch(context, -1, operator)
            entity.remove_objects()

            deps = get_sketch_deps_indecies(entity, context)
            deps.sort(reverse=True)

            for i in deps:
                operator.delete(entities.get(i), context)

        elif is_referenced(entity, context):
            operator.report(
                {"WARNING"},
                "Cannot delete {}, other entities depend on it.".format(entity),
            )
            return {"CANCELLED"}

        operator.delete(entity, context)

    @staticmethod
    def delete(entity, context):
        # TODO: Some data (Select state, hover, ...) is stored based on index,
        # Clear that data when changing pointers!

        entity.selected = False

        # Delete constraints that depend on entity
        constraints = context.scene.sketcher.constraints

        for data_coll, indices in get_constraint_local_indices(entity, context):
            if not indices:
                continue
            indices.sort(reverse=True)
            for i in indices:
                logger.debug("Delete: {}".format(data_coll[i]))
                data_coll.remove(i)

        logger.debug("Delete: {}".format(entity))
        entities = context.scene.sketcher.entities
        entities.remove(entity.slvs_index)

    def execute(self, context):
        index = self.index

        if index != -1:
            self.main(context, index, self)
        else:
            indices = []
            for e in context.scene.sketcher.entities.selected_entities:
                indices.append(e.slvs_index)

            indices.sort(reverse=True)
            for i in indices:
                e = context.scene.sketcher.entities.get(i)

                # NOTE: this might be slow when alot of entities are selected, improve!
                if is_referenced(e, context):
                    continue
                self.delete(e, context)

        functions.refresh(context)
        return {"FINISHED"}


from .global_data import WpReq

state_docstr = "Pick entity to constrain."


class GenericConstraintOp(GenericEntityOp):
    initialized: BoolProperty(options={"SKIP_SAVE", "HIDDEN"})
    _entity_prop_names = ("entity1", "entity2", "entity3", "entity4")

    @classmethod
    def poll(cls, context):
        return True

    def _available_entities(self):
        # Gets entities that are already set
        cls = class_defines.SlvsConstraints.cls_from_type(self.type)
        entities = [None] * len(cls.signature)
        for i, name in enumerate(self._entity_prop_names):
            if hasattr(self, name):
                e = getattr(self, name)
                if not e:
                    continue
                entities[i] = e
        return entities

    @classmethod
    def states(cls, operator=None):
        states = []

        cls_constraint = class_defines.SlvsConstraints.cls_from_type(cls.type)

        for i, _ in enumerate(cls_constraint.signature):
            name_index = i + 1
            if hasattr(cls_constraint, "get_types") and operator:
                types = cls_constraint.get_types(i, *operator._available_entities())
            else:
                types = cls_constraint.signature[i]


            states.append(
                state_from_args(
                    "Entity " + str(name_index),
                    description=state_docstr,
                    pointer="entity" + str(name_index),
                    property=None,
                    types=types,
                )
            )
        return states

    def initialize_constraint(self):
        c = self.target
        if not self.initialized and hasattr(c, "init_props"):
            value, setting = c.init_props()
            if value is not None:
                self.value = value
            if setting is not None:
                self.setting = setting
        self.initialized = True

    @classmethod
    def description(cls, context, properties):
        constraint_type = cls.type
        cls_constraint = class_defines.SlvsConstraints.cls_from_type(constraint_type)

        states = [state_desc(s.name, s.description, s.types) for s in cls.get_states_definition()]

        return stateful_op_desc("Add {} constraint".format(cls_constraint.label), *states)

    def fill_entities(self):
        c = self.target
        args = []
        # fill in entities!
        for prop in self._entity_prop_names:
            if hasattr(c, prop):
                value = getattr(self, prop)
                setattr(c, prop, value)
                args.append(value)
        return args

    def main(self, context):
        c = self.target = context.scene.sketcher.constraints.new_from_type(self.type)

        self.sketch = context.scene.sketcher.active_sketch

        entities = self.fill_entities()
        c.sketch = self.sketch

        if self.type == "COINCIDENT":
            # TODO: Implicitly merge points
            if all([type(e) in class_defines.point for e in entities]):
                context.scene.sketcher.constraints.remove(c)
                return False

        self.initialize_constraint()

        if hasattr(c, "value"):
            c.value = self.value
        if hasattr(c, "setting"):
            c.setting = self.setting

        deselect_all(context)
        solve_system(context, sketch=self.sketch)
        functions.refresh(context)
        return True

    def fini(self, context, succeede):
        if hasattr(self, "target"):
            logger.debug("Add: {}".format(self.target))

    def draw(self, context):
        layout = self.layout

        c = self.target
        if not c:
            return

        if hasattr(c, "value"):
            layout.prop(self, "value")
        if hasattr(c, "setting"):
            layout.prop(self, "setting")


# Dimensional constarints
class VIEW3D_OT_slvs_add_distance(
    Operator, GenericConstraintOp
):
    bl_idname = "view3d.slvs_add_distance"
    bl_label = "Distance"
    bl_options = {"UNDO", "REGISTER"}

    value: FloatProperty(
        name="Distance", subtype="DISTANCE", unit="LENGTH", options={"SKIP_SAVE"}
    )
    type = "DISTANCE"


class VIEW3D_OT_slvs_add_angle(
    Operator, GenericConstraintOp
):
    bl_idname = "view3d.slvs_add_angle"
    bl_label = "Angle"
    bl_options = {"UNDO", "REGISTER"}

    value: FloatProperty(
        name="Angle", subtype="ANGLE", unit="ROTATION", options={"SKIP_SAVE"}
    )
    setting: BoolProperty(name="Invert")
    type = "ANGLE"


class VIEW3D_OT_slvs_add_diameter(
    Operator, GenericConstraintOp
):
    bl_idname = "view3d.slvs_add_diameter"
    bl_label = "Diameter"
    bl_options = {"UNDO", "REGISTER"}

    value: FloatProperty(
        name="Diameter", subtype="DISTANCE", unit="LENGTH", options={"SKIP_SAVE"}
    )
    type = "DIAMETER"


# Geomteric constraints
class VIEW3D_OT_slvs_add_coincident(
    Operator, GenericConstraintOp
):
    bl_idname = "view3d.slvs_add_coincident"
    bl_label = "Coincident"
    bl_options = {"UNDO", "REGISTER"}

    type = "COINCIDENT"


class VIEW3D_OT_slvs_add_equal(
    Operator, GenericConstraintOp
):
    bl_idname = "view3d.slvs_add_equal"
    bl_label = "Equal"
    bl_options = {"UNDO", "REGISTER"}

    type = "EQUAL"


class VIEW3D_OT_slvs_add_vertical(
    Operator, GenericConstraintOp
):
    bl_idname = "view3d.slvs_add_vertical"
    bl_label = "Vertical"
    bl_options = {"UNDO", "REGISTER"}

    type = "VERTICAL"


class VIEW3D_OT_slvs_add_horizontal(
    Operator, GenericConstraintOp
):
    bl_idname = "view3d.slvs_add_horizontal"
    bl_label = "Horizontal"
    bl_options = {"UNDO", "REGISTER"}

    type = "HORIZONTAL"


class VIEW3D_OT_slvs_add_parallel(
    Operator, GenericConstraintOp
):
    bl_idname = "view3d.slvs_add_parallel"
    bl_label = "Parallel"
    bl_options = {"UNDO", "REGISTER"}

    type = "PARALLEL"


class VIEW3D_OT_slvs_add_perpendicular(
    Operator, GenericConstraintOp
):
    bl_idname = "view3d.slvs_add_perpendicular"
    bl_label = "Perpendicular"
    bl_options = {"UNDO", "REGISTER"}

    type = "PERPENDICULAR"


class VIEW3D_OT_slvs_add_tangent(
    Operator, GenericConstraintOp, GenericEntityOp
):
    bl_idname = "view3d.slvs_add_tangent"
    bl_label = "Tangent"
    bl_options = {"UNDO", "REGISTER"}

    type = "TANGENT"


class VIEW3D_OT_slvs_add_midpoint(
    Operator, GenericConstraintOp, GenericEntityOp
):
    bl_idname = "view3d.slvs_add_midpoint"
    bl_label = "Midpoint"
    bl_options = {"UNDO", "REGISTER"}

    type = "MIDPOINT"


class VIEW3D_OT_slvs_add_ratio(
    Operator, GenericConstraintOp, GenericEntityOp
):

    value: FloatProperty(
        name="Ratio", subtype="UNSIGNED", options={"SKIP_SAVE"}, min=0.0
    )
    bl_idname = "view3d.slvs_add_ratio"
    bl_label = "Ratio"
    bl_options = {"UNDO", "REGISTER"}

    type = "RATIO"


class View3D_OT_slvs_delete_constraint(Operator, HighlightElement):
    """Delete constraint by type and index
    """
    bl_idname = "view3d.slvs_delete_constraint"
    bl_label = "Delete Constraint"
    bl_options = {"UNDO"}
    bl_description = "Delete Constraint"

    type: StringProperty(name="Type")
    index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return True

    @classmethod
    def description(cls, context, properties):
        cls.handle_highlight_hover(context, properties)
        if properties.type:
            return "Delete: " + properties.type.capitalize()
        return ""

    def execute(self, context):
        constraints = context.scene.sketcher.constraints

        # NOTE: It's not really neccesary to first get the
        # constraint from it's index before deleting

        constr = constraints.get_from_type_index(self.type, self.index)
        logger.debug("Delete: {}".format(constr))

        constraints.remove(constr)

        sketch = context.scene.sketcher.active_sketch
        solve_system(context, sketch=sketch)
        functions.refresh(context)
        return {"FINISHED"}


class View3D_OT_slvs_tweak_constraint_value_pos(Operator):
    bl_idname = "view3d.slvs_tweak_constraint_value_pos"
    bl_label = "Tweak Constraint"
    bl_options = {"UNDO"}
    bl_description = "Tweak constraint's value or display position"

    type: StringProperty(name="Type")
    index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return True

    def invoke(self, context, event):
        self.tweak = False
        self.init_mouse_pos = Vector((event.mouse_region_x, event.mouse_region_y))
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        delta = (
            self.init_mouse_pos - Vector((event.mouse_region_x, event.mouse_region_y))
        ).length
        if not self.tweak and delta > 6:
            self.tweak = True

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            if not self.tweak:
                self.execute(context)
            return {"FINISHED"}

        if not self.tweak:
            return {"RUNNING_MODAL"}

        coords = event.mouse_region_x, event.mouse_region_y

        constraints = context.scene.sketcher.constraints
        constr = constraints.get_from_type_index(self.type, self.index)

        origin, end_point = functions.get_picking_origin_end(context, coords)
        pos = intersect_line_plane(origin, end_point, *constr.draw_plane())

        mat = constr.matrix_basis()
        pos = mat.inverted() @ pos

        constr.update_draw_offset(pos, context.preferences.system.ui_scale)
        context.space_data.show_gizmo = True
        return {"RUNNING_MODAL"}

    def execute(self, context):
        bpy.ops.view3d.slvs_context_menu(type=self.type, index=self.index)
        return {"FINISHED"}


from bl_operators.presets import AddPresetBase


class SKETCHER_OT_add_preset_theme(AddPresetBase, Operator):
    """Add an Theme Preset"""

    bl_idname = "bgs.theme_preset_add"
    bl_label = "Add Theme Preset"
    preset_menu = "SKETCHER_MT_theme_presets"

    preset_defines = [
        'prefs = bpy.context.preferences.addons["CAD_Sketcher"].preferences',
        "theme = prefs.theme_settings",
        "entity = theme.entity",
        "constraint = theme.constraint",
    ]

    preset_values = [
        "entity.default",
        "entity.highlight",
        "entity.selected",
        "entity.selected_highlight",
        "entity.inactive",
        "entity.inactive_selected",
        "constraint.default",
        "constraint.highlight",
        "constraint.failed",
        "constraint.failed_highlight",
        "constraint.text",
    ]

    preset_subdir = "bgs/theme"

def mesh_from_temporary(mesh, name):
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)

    bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(0.1), verts=bm.verts, edges=bm.edges)

    new_mesh = bpy.data.meshes.new(name)
    bm.to_mesh(new_mesh)
    bm.free()
    return new_mesh


def _cleanup_data(sketch, mode):
    if sketch.target_mesh and mode != "MESH":
        sketch.target_mesh = None
    if sketch.target_object and mode != "MESH":
        sketch.target_object.sketch_index = -1
        bpy.data.objects.remove(sketch.target_object, do_unlink=True)
        sketch.target_object = None
    if sketch.target_curve and mode != "BEZIER":
        sketch.target_curve = None
    if sketch.target_curve_object and mode != "BEZIER":
        sketch.target_curve_object.sketch_index = -1
        bpy.data.objects.remove(sketch.target_curve_object, do_unlink=True)
        sketch.target_curve_object = None

def _ensure_curve_data(sketch):
    if not sketch.target_curve:
        curve = bpy.data.objects.data.curves.new(sketch.name, "CURVE")
        sketch.target_curve = curve
        return curve

    # Clear existing curve data
    sketch.target_curve.splines.clear()
    return sketch.target_curve

def _link_unlink_object(scene, ob, keep):
    objects = scene.collection.objects
    exists = ob.name in objects

    if exists:
        if not keep:
            objects.unlink(ob)
    elif keep:
        objects.link(ob)

def update_convertor_geometry(scene):
    for sketch in scene.sketcher.entities.sketches:
        mode = sketch.convert_type
        if sketch.convert_type == "NONE":
            _cleanup_data(sketch, mode)
            continue

        data = bpy.data
        name = sketch.name

        # Convert geometry to curve data
        conv = convertors.BezierConvertor(scene, sketch)
        conv.run()
        # TODO: Avoid re-converting sketches where nothing has changed!
        logger.info("Convert sketch {} to {}: ".format(sketch, mode.lower()))
        curve_data = _ensure_curve_data(sketch)
        conv.to_bezier(curve_data)
        data = curve_data


        # Create curve object
        if not sketch.target_curve_object:
            object = bpy.data.objects.new(name, curve_data)
            sketch.target_curve_object = object

        # Link / unlink curve object
        _link_unlink_object(scene, sketch.target_curve_object, mode == "BEZIER")


        if mode == "MESH":
            # Create mesh data
            temp_mesh = sketch.target_curve_object.to_mesh()
            mesh = mesh_from_temporary(temp_mesh, name)
            sketch.target_curve_object.to_mesh_clear()

            sketch.target_mesh = mesh.copy()

            # Create mesh object
            if not sketch.target_object:
                mesh_object = bpy.data.objects.new(name, sketch.target_mesh)
                scene.collection.objects.link(mesh_object)
                sketch.target_object = mesh_object
            else:
                sketch.target_object.data = sketch.target_mesh


        _cleanup_data(sketch, mode)

        target_ob = sketch.target_object if mode == "MESH" else sketch.target_curve_object
        target_ob.matrix_world = sketch.wp.matrix_basis

        target_ob.sketch_index = sketch.slvs_index


constraint_operators = (
    VIEW3D_OT_slvs_add_distance,
    VIEW3D_OT_slvs_add_diameter,
    VIEW3D_OT_slvs_add_angle,
    VIEW3D_OT_slvs_add_coincident,
    VIEW3D_OT_slvs_add_equal,
    VIEW3D_OT_slvs_add_vertical,
    VIEW3D_OT_slvs_add_horizontal,
    VIEW3D_OT_slvs_add_parallel,
    VIEW3D_OT_slvs_add_perpendicular,
    VIEW3D_OT_slvs_add_tangent,
    VIEW3D_OT_slvs_add_midpoint,
    VIEW3D_OT_slvs_add_ratio,
)

classes = (
    View3D_OT_slvs_register_draw_cb,
    View3D_OT_slvs_unregister_draw_cb,
    View3D_OT_slvs_select,
    View3D_OT_slvs_select_all,
    View3D_OT_slvs_context_menu,
    View3D_OT_slvs_show_solver_state,
    View3D_OT_slvs_tweak,
    View3D_OT_slvs_add_point3d,
    VIEW3D_OT_slvs_write_selection_texture,
    View3D_OT_slvs_add_line3d,
    View3D_OT_slvs_add_workplane,
    View3D_OT_slvs_add_workplane_face,
    View3D_OT_slvs_add_sketch,
    View3D_OT_slvs_add_point2d,
    View3D_OT_slvs_add_line2d,
    View3D_OT_slvs_add_circle2d,
    View3D_OT_slvs_add_arc2d,
    View3D_OT_slvs_add_rectangle,
    View3D_OT_slvs_test,
    View3D_OT_invoke_tool,
    View3D_OT_slvs_set_active_sketch,
    View3D_OT_slvs_delete_entity,
    *constraint_operators,
    View3D_OT_slvs_solve,
    View3D_OT_slvs_delete_constraint,
    View3D_OT_slvs_tweak_constraint_value_pos,
    SKETCHER_OT_add_preset_theme,
)


def register():
    for cls in classes:
        if issubclass(cls, StatefulOperator):
            cls.register_properties()

        bpy.utils.register_class(cls)


def unregister():
    offscreen = global_data.offscreen
    if offscreen:
        offscreen.free()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
