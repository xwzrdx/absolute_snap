bl_info = {
    "name": "Absolute Snap",
    "author": "Nik Bartlett",
    "version": (1, 0),
    "blender": (3, 0, 0),
    "description": "Copy/paste in world space and swap constraints effortlessly",
    "category": "Animation",
}

# This tool is created with inspiration from Pataz (https://github.com/PatazAnimation/pataz-anim-toolz)
# With help from Dustin (discord: dstn.r, twt: @dustin_01, gumroad: https://dstn.gumroad.com/)

import bpy
import math
from mathutils import Matrix


NB_matrix = [] # For storing the target's world matrix when copying
NB_relative = [] # For storing the relationship between two objects
NB_current_constraint = '' # For storing the current edited constraint, right now just for the right click menu
NB_eval_message = '' # For the evaluation error message

##### Functions #####

#wzrd: new function
def get_action_fcurves(action):
    """Return fcurves from an Action, supporting both legacy and Blender 5+ layered actions."""
    if not action:
        return []

    fcurves = []

    # New layered/slotted actions
    layers = getattr(action, "layers", None)
    if layers:
        for layer in layers:
            for strip in getattr(layer, "strips", []):
                for channelbag in getattr(strip, "channelbags", []):
                    for fcu in getattr(channelbag, "fcurves", []):
                        fcurves.append(fcu)

    # If we found any via layers, use those
    if fcurves:
        return fcurves

    # Legacy API (pre-5.0 im pretty sure lol) 
    legacy_fcurves = getattr(action, "fcurves", None)
    if legacy_fcurves is not None:
        return list(legacy_fcurves)

    return []

#####


# Return true if the given object is in pose mode
def object_in_posemode(obj):
    return ((obj.type == 'ARMATURE') & (obj.mode == 'POSE'))

# Check if we currently have an active object or pose bone selected
def active_check(context):
    valid = False
    object = context.active_object
    if object:
        if object_in_posemode(object):
            if (context.selected_pose_bones and context.active_pose_bone):
                valid = True
        else:
            if context.selected_objects:
                valid = True
    return valid

# Get the current active object or bone, doesn't need to be selected
def get_obj(context):
    obj = context.active_object
    if object_in_posemode(obj):
        obj = context.active_pose_bone
    return obj

# Return lists containing all the safely evaluated constraints, and all the influences
def get_channels(obj):
    
    safe_constraints = []
    influences = []
    global NB_eval_message
    
    for con in obj.constraints:
        if valid_constraint(con):
            channels = [con.use_location_x, con.use_location_y, con.use_location_z,
                        con.use_rotation_x, con.use_rotation_y, con.use_rotation_z]
            im = con.inverse_matrix.copy()
            t1, r1, sc1 = Matrix.decompose(im)
            valid_scale = True
            for axis in sc1:
                if not math.isclose(sc1.x, axis, abs_tol=0.00001):
                    valid_scale = False
            influence = con.influence
            if (influence != 1 and influence != 0) or (any(channels) and not all(channels)) or not valid_scale:
                safe_constraints.append(False)
                if not valid_scale:
                    NB_eval_message = 'Inverse scale not equal'
                elif (influence != 1 and influence != 0):
                    NB_eval_message = 'Influence is not 0 or 1'
                else:
                    NB_eval_message = 'Loc/Rot channels disabled'
            else:
                safe_constraints.append(True)
            influences.append(influence)

            
    return safe_constraints, influences

# Get the matrix world of the given object
# If it's a bone we multiply by the armature's matrix to get the worldspace
def get_matrix(context, object):
    
    if object_in_posemode(object):
        matrix = object.matrix_world.copy() @ context.active_pose_bone.matrix.copy()
    else:
        matrix = object.matrix_world.copy()
    return matrix

# Return the correct items for valid multiple selection usage
# We want to perform functions only on the ones the user intends
def get_selection(context):
    
    parent = ''
    child = ''
    child_armature = ''
    bone = False
    
    active = context.active_object
    selection = context.selected_objects
    active_bone = context.active_pose_bone
    bone_selection = context.selected_pose_bones
    
    if selection is not None:
        if len(selection) == 1 and bone_selection is not None and len(bone_selection) == 2:
            parent = active_bone
            for bone in bone_selection:
                if bone != parent:
                    child = bone
                    child_armature = active
                    bone = True
        elif len(selection) == 2:
            armature_count = 0
            for object in selection:
                if object_in_posemode(object):
                    armature_count += 1
            if armature_count == 2:
                if bone_selection is not None and len(bone_selection) == 2:
                    parent = active_bone
                    for bone in bone_selection:
                        if bone != parent:
                            child = bone
                            child_armature = child.id_data
                            bone = True
            elif armature_count == 1:
                if bone_selection is not None and len(bone_selection) >= 1:
                    parent = active_bone
                for object in selection:
                    if object.type != 'ARMATURE':
                        child = object
            else:
                parent = active
                for object in selection:
                    if object != parent:
                        child = object
    
    return parent, child, child_armature, bone

# Return a list of constraints plus their keyed state for populating the UI correctly
def constraint_list_items(scene, context):

    name = context.scene.my_tool.name_checkbox
    obj = get_obj(context)
    items = []
    
    fcurve_prefix = ''
    if context.selected_pose_bones and context.active_pose_bone:
        fcurve_prefix = f'pose.bones["{obj.name}"].'
        
    for con in obj.constraints:
        if valid_constraint(con):
            enum_name = con.subtarget if con.subtarget else str(con.target.name)
            if name:
                enum_name = con.name
            icon = 'DECORATE'
            fcurve_name = f'{fcurve_prefix}constraints["{con.name}"].influence'
            if con.id_data.animation_data:
                frame = bpy.context.scene.frame_current
                action = con.id_data.animation_data.action
                if action is not None:
                    fcurves = get_action_fcurves(action) #wzrd: instead of fcurves = con.id_data.animation_data.action.fcurves
                    for fcurve in fcurves:
                        if fcurve.data_path == fcurve_name:
                            icon = 'DECORATE_ANIMATE'
                            for keyframe in fcurve.keyframe_points:
                                if keyframe.co[0] == frame:
                                    icon = 'DECORATE_KEYFRAME'  
            items.append((enum_name, icon))
            
    return items

# Change the given constraint's influence based on arguments
def change_influence(con, name, item, enable):
    A = 1.0 if enable else 0.0
    con.influence = A if name == item else 0.0
    
# Return true if the given constraint is contributing to the evaluation (even if influence is 0)
def valid_constraint(con):
    return con.type == 'CHILD_OF' and con.enabled and con.target

# Main function for getting the evaluated matrices of each constraint on the object, returned as a list
def calculate_childof(object):
       
    matrices = []
    
    for con in object.constraints:
        if valid_constraint(con) and con.influence:

            location = [con.use_location_x, con.use_location_y, con.use_location_z]
            rotation = [con.use_rotation_x, con.use_rotation_y, con.use_rotation_z]
            scale = [con.use_scale_x, con.use_scale_y, con.use_scale_z]
            #influence = con.influence
            
            pm = con.target.matrix_world.copy()
            if con.subtarget:
                pm = pm @ con.target.pose.bones[con.subtarget].matrix
            im = con.inverse_matrix.copy()
            
            # Right now we're only setting the scale to identity for both the parent matrix
            # and the inverse matrix, until we have a way of getting an inversely evaluated basis
            # in order to allow partial influence and partial loc rot channels.
            # If you're reading this and want to figure it out, please talk to Nikos#7542.
            # As a heads up, it's very very tricky.
            
            t, r, sc = Matrix.decompose(pm)
            t1, r1, sc1 = Matrix.decompose(im)
            new_scale = [1.0 if not i else sc[index] for index, i in enumerate(scale)]
            new_scale1 = [1.0 if not i else sc1[index] for index, i in enumerate(scale)]
        
            pm = Matrix.LocRotScale(t, r, new_scale)
            im = Matrix.LocRotScale(t1, r1, new_scale1)
            pm = im.inverted() @ pm.inverted()

            matrices.append(pm)
                
    return matrices

# Modifying and applying the matrices in order to snap the object correctly
def apply_snap(matrices, matrix, object, armature, bone):
    
    if matrices:
        result = matrices[0]
        for m in matrices[1:]:
            result = result @ m
        result = result @ matrix
        t, r, s = result.decompose()
        if bone:
            result_matrix = Matrix.LocRotScale(t, r, object.scale)
            object.matrix = result_matrix
            #print('bone constraint')
        else:
            result = Matrix.LocRotScale(t, r, object.matrix_basis.to_scale())
            object.matrix_basis = result
            #print('object constraint')
    else:
        if bone:
            final_matrix = armature.matrix_world.inverted() @ matrix
            t, r, s = final_matrix.decompose()
            final_matrix = Matrix.LocRotScale(t, r, object.scale)
            object.matrix = final_matrix
            #print('bone no constraint')
        else:
            t, r, s = Matrix.decompose(matrix)
            matrix = Matrix.LocRotScale(t, r, object.matrix_world.to_scale())
            object.matrix_world = matrix
            #print('object no constraint')
      
# Set keys on all channels except for scale        
def key_object(obj):
    rot_mode = obj.rotation_mode
    obj.keyframe_insert(data_path='location')
    if rot_mode == 'QUATERNION':
        obj.keyframe_insert(data_path='rotation_quaternion')
    elif rot_mode == 'AXIS_ANGLE':
        obj.keyframe_insert(data_path='rotation_axis_angle')
    else:
        obj.keyframe_insert(data_path='rotation_euler')
    #obj.keyframe_insert(data_path='scale')

# Remove keys on all channels except for scale
def unkey_object(obj):
    rot_mode = obj.rotation_mode
    obj.keyframe_delete(data_path='location')
    if rot_mode == 'QUATERNION':
        obj.keyframe_delete(data_path='rotation_quaternion')
    elif rot_mode == 'AXIS_ANGLE':
        obj.keyframe_delete(data_path='rotation_axis_angle')
    else:
        obj.keyframe_delete(data_path='rotation_euler')
    #obj.keyframe_delete(data_path='scale')
    
# Redraw a few areas when called in order for keyframe related UI to be up to date
def refresh_anim():
    for area in bpy.context.screen.areas:
        if area.type in {'TIMELINE', 'DOPESHEET_EDITOR', 'GRAPH_EDITOR', 'NLA_EDITOR'}:
            area.tag_redraw()


##### CLASSES #####
class NBASProperties(bpy.types.PropertyGroup):
    
    snap_checkbox : bpy.props.BoolProperty(
        name = "", 
        description = "Autosnap - Keeps world position the same when you enable or\ndisable a constraint. Evaluation must be safe in order to work", 
        default = True)
        
    name_checkbox : bpy.props.BoolProperty(
        name = "Use_name", 
        description = "If enabled, will list the names of your Child Of constraints instead of their targets or subtargets", 
        default = False)
        
    link_checkbox : bpy.props.BoolProperty(
        name = "Link_mode", 
        description = "If enabled, will allow only one constraint to be active at a time", 
        default = True)
        
class NB_Absolute_Snap_ui(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'NBTools'
    bl_label = "Absolute Snap"
    bl_idname = "NB_PT_Absolute_Snap_ui"

    @classmethod
    def poll(self, context):
        
        return context.object

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        mytool = scene.my_tool
        obj = get_obj(context)
        name = context.scene.my_tool.name_checkbox
        link = context.scene.my_tool.link_checkbox
        global NB_eval_message
        
        layout.enabled = bool(context.selected_objects)
        
        eval_row = layout.row()
        eval_row2 = layout.row()
        safe, influences = get_channels(obj)
        if all(safe):
            eval_row.enabled = False
            eval_row.label(text="Evaluated safely", icon="FAKE_USER_ON")
            NB_eval_message = ''
        else:
            eval_row.enabled = True
            eval_row.alert = True
            eval_row.label(text="Evaluation error - Copy only!", icon="ERROR")
            if NB_eval_message:
                eval_row2.alert = True
                eval_row2.label(text=NB_eval_message, icon="BLANK1")
        
        copypaste_row = layout.row()
        copypaste_row.operator(COPY_XFORM.bl_idname, icon="DUPLICATE")
        copypaste_row.operator(PASTE_XFORM.bl_idname, icon="BRUSH_DATA")
        
        snapselect_row = layout.row()
        snapselect_row.operator(SNAP_SELECTED.bl_idname, icon="CON_LOCLIKE")
        
        relative_row = layout.row()
        split = relative_row.split(factor=0.5)
        split.operator(COPY_RELATIVE.bl_idname, text='Relative', icon="CON_CHILDOF")
        
        relative_row = split.split()
        left = relative_row.operator(PASTE_RELATIVE.bl_idname, text='', icon="BACK")
        left.paste_direction = -1
        paste = relative_row.operator(PASTE_RELATIVE.bl_idname, text='', icon="CON_CHILDOF")
        paste.paste_direction = 0
        right = relative_row.operator(PASTE_RELATIVE.bl_idname, text='', icon="FORWARD")
        right.paste_direction = 1
        
        constraint_box = layout.box()
        coc = False
        for con in obj.constraints:
            if con.type == 'CHILD_OF':
                coc = True
                break
        valid = active_check(context)
        constraint_box.enabled = coc and valid
        
        items = constraint_list_items(scene, context)
        
        if items and valid:

            text_row = constraint_box.row()
            text_row.label(text=" Constraints:")
            if bpy.context.scene.tool_settings.use_keyframe_insert_auto == True:
                autokey_col = text_row.column()
                autokey_col.enabled = False
                autokey_col.alignment = 'RIGHT'
                autokey_col.label(text="Autokey on")

            for index, item in enumerate(items):
                
                con_box = constraint_box.box()
                con_box.ui_units_y += 1.05
                con_row = con_box.row()
                
                con_row1 = con_row.row()
                con_row1.scale_x = 1.2
                if influences[index] == 0:
                    off = con_row1.operator(TOGGLE_CONSTRAINT.bl_idname, icon='QUIT')
                    off.item = item[0]
                    off.enable = True
                    off.disable = False
                else:
                    on = con_row1.operator(TOGGLE_CONSTRAINT.bl_idname, icon='QUIT', depress=True)
                    on.item = item[0]
                    on.enable = False
                    on.disable = False   
                    
                con_row2 = con_row.row()
                if not safe[index]:
                    con_row2.alert = True
                op = con_row2.operator(CON_LCMENU.bl_idname, text=f' {item[0]}', emboss=False)
                op.item = item[0]
                
                con_row3 = con_row.row()
                con_row3.scale_x = 0.9
                con_row3.ui_units_x += 1
                if item[1] != 'DECORATE_KEYFRAME':
                    setkey = con_row3.operator(KEY_CONSTRAINT.bl_idname, text='', icon=item[1], emboss=False)
                    setkey.item = item[0]
                else:
                    delkey = con_row3.operator(UNKEY_CONSTRAINT.bl_idname, text='', icon=item[1], emboss=False)
                    delkey.item = item[0]
                    
            constraint_box.separator(factor=0)
            disable = constraint_box.operator(TOGGLE_CONSTRAINT.bl_idname, text='Disable All', icon='UNLINKED')
            disable.item = ''
            disable.enable = False
            disable.disable = True
            
            keying_row = constraint_box.row()
            split = keying_row.split(factor=0.5)
            split.operator(KEY_ALL.bl_idname, icon="KEY_HLT")
            split.operator(UNKEY_ALL.bl_idname, icon="KEY_DEHLT")
            split.prop(mytool, "snap_checkbox", text="", icon="SNAP_ON") 
            
            options_row = constraint_box.row()
            options_row.prop(mytool, "name_checkbox", text="Name") 
            options_row.prop(mytool, "link_checkbox", text="Link") 
        else:
            constraint_box.label(text=" Object has no constraints")
            
class NB_Absolute_Snap_lcmenu(bpy.types.Menu):
    bl_label = "Influence"
    bl_idname = "OBJECT_MT_NBAS_lcmenu"

    def draw(self, context):
        global NB_current_constraint
        
        layout = self.layout

        op = layout.operator(KEY_CONSTRAINT.bl_idname, text='Insert Keyframe', icon="KEY_HLT")
        op.item=NB_current_constraint
        layout.separator()
        op = layout.operator(UNKEY_CONSTRAINT.bl_idname, text='Delete Keyframe', icon="KEY_DEHLT")
        op.item=NB_current_constraint

class CON_LCMENU(bpy.types.Operator):
    bl_idname = "absolutesnap.conlcmenu"
    bl_description = "Child Of Constraint. Left click to open menu"
    bl_label = ""
    
    item : bpy.props.StringProperty(default='', options={'HIDDEN'})

    @classmethod
    def poll(self, context):
        valid = active_check(context)
        return valid

    def execute(self, context):
        global NB_current_constraint
        NB_current_constraint = self.item
        bpy.ops.wm.call_menu(name="OBJECT_MT_NBAS_lcmenu")
        return {'FINISHED'}

class COPY_XFORM(bpy.types.Operator):
    bl_idname = "absolutesnap.copyxform"
    bl_description = "Copy the absolute transform of the active object"
    bl_label = "Copy"

    @classmethod
    def poll(self, context):
        valid = active_check(context)
        return valid

    def execute(self, context):
        global NB_matrix
        object = context.active_object
        NB_matrix = get_matrix(context, object) 
        return {'FINISHED'}

class PASTE_XFORM(bpy.types.Operator):
    bl_idname = "absolutesnap.pastexform"
    bl_description = "Paste the copied xform onto the active object"
    bl_label = "Paste"
    bl_options = {"REGISTER", "UNDO"}
    
    @classmethod
    def poll(self, context):
        valid = active_check(context)
        return NB_matrix and valid

    def execute(self, context):
        global NB_matrix
        object = context.active_object
        armature = ''
        bone = False
        if object_in_posemode(object):
            bone = True
            armature = object
            object = context.active_pose_bone 

        matrices = calculate_childof(object)        
        apply_snap(matrices, NB_matrix, object, armature, bone)
        
        if bpy.context.scene.tool_settings.use_keyframe_insert_auto == True:
            key_object(object)
            
        return {'FINISHED'}

class SNAP_SELECTED(bpy.types.Operator):
    bl_idname = "absolutesnap.snapselected"
    bl_description = "Snap the selected object(s) to the active object.\nNote: Related objects will not snap correctly"
    bl_label = "Snap selected to active"
    bl_options = {"REGISTER", "UNDO"}
    
    @classmethod
    def poll(self, context):
        valid = False
        
        selection = context.selected_objects
        bone_selection = context.selected_pose_bones
        
        if selection is not None:
            if len(selection) == 1 and bone_selection is not None and len(bone_selection) >= 2:
                valid = True
            elif len(selection) >= 2:
                valid = True
        
        return valid

    def execute(self, context):
        
        target = context.active_object
        matrix = get_matrix(context, target)
        bone = True if object_in_posemode(target) else False
        
        if bone:
            for pose_bone in context.selected_pose_bones:
                if pose_bone != context.active_pose_bone:
                    matrices = calculate_childof(pose_bone)        
                    apply_snap(matrices, matrix, pose_bone, target, bone)
                    if bpy.context.scene.tool_settings.use_keyframe_insert_auto == True:
                        key_object(pose_bone)
                    
        for obj in context.selected_objects:
            if not object_in_posemode(obj) and obj != target:
                matrices = calculate_childof(obj)        
                apply_snap(matrices, matrix, obj, target, False)
                if bpy.context.scene.tool_settings.use_keyframe_insert_auto == True:
                    key_object(obj)
        
        return {'FINISHED'}
    
class COPY_RELATIVE(bpy.types.Operator):
    bl_idname = "absolutesnap.copyrelative"
    bl_description = "Copy the relationship between the selected and active objects"
    bl_label = "Relative"
    
    @classmethod
    def poll(self, context):
        parent, child, child_armature, bone = get_selection(context)
        return parent != ''

    def execute(self, context):
        global NB_relative
        
        parent = context.active_object
        parent_matrix = get_matrix(context, parent) 
        
        x, child, child_armature, bone = get_selection(context)
            
        if bone:
            child_matrix = child_armature.matrix_world @ child.matrix
        else:
            child_matrix = child.matrix_world.copy()
            
        NB_relative = parent_matrix.inverted() @ child_matrix
        return {'FINISHED'}
    
class PASTE_RELATIVE(bpy.types.Operator):
    bl_idname = "absolutesnap.pasterelative"
    bl_description = "Paste the copied relationship onto the selection, relative to the active object.\nShift click to bake along the entire timeline"
    bl_label = ""
    bl_options = {"REGISTER", "UNDO"}
    
    paste_direction : bpy.props.IntProperty(default=0, options={'HIDDEN'})
    bake : bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    frame_current : bpy.props.IntProperty(default=0, options={'HIDDEN'})
    
    @classmethod
    def poll(self, context):
        parent, child, child_armature, bone = get_selection(context)
        return NB_relative and parent != ''
    
    @classmethod
    def description(cls, context, properties):
        if properties.paste_direction == -1:
            return 'Paste relative backward by 1 frame and set key.\nShift click to bake backwards'
        elif properties.paste_direction == 1:
            return 'Paste relative forward by 1 frame and set key.\nShift click to bake forwards'

    def execute(self, context):
        global NB_relative
        
        if self.paste_direction != 0:
            self.frame_current = bpy.context.scene.frame_current
            bpy.context.scene.frame_set(self.frame_current + self.paste_direction)
            self.bake = True
        bpy.context.view_layer.update()
        
        parent, child, child_armature, bone = get_selection(context)
        matrix = get_matrix(context, parent.id_data) @ NB_relative

        matrices = calculate_childof(child)        
        apply_snap(matrices, matrix, child, child_armature, bone)
        
        if bpy.context.scene.tool_settings.use_keyframe_insert_auto == True or self.bake:
            x, child, y, z = get_selection(context)
            key_object(child)
            
        return {'FINISHED'}
    
    def invoke(self, context, event):

        if event.shift:
            self.bake = True
            frame_start = bpy.context.scene.frame_start 
            self.frame_current = bpy.context.scene.frame_current
            frame_current_reference = self.frame_current
            frame_end = bpy.context.scene.frame_end 
            if self.paste_direction == -1:    
                while self.frame_current > frame_start + 1:
                    self.execute(context)
            else:
                if self.paste_direction == 0:
                    bpy.context.scene.frame_set(frame_start - 1)  
                    self.paste_direction = 1     
                while self.frame_current < frame_end - 1:
                    self.execute(context)
            bpy.context.scene.frame_set(frame_current_reference) 
            self.paste_direction = 0 
            self.execute(context)
        else:
            self.bake = False
            self.execute(context)
        
        return {'FINISHED'}
    
class TOGGLE_CONSTRAINT(bpy.types.Operator):
    bl_idname = "absolutesnap.toggleconstraint"
    bl_description = "Toggle"
    bl_label = ""
    bl_options = {"REGISTER", "UNDO"}
    
    item : bpy.props.StringProperty(default='', options={'HIDDEN'})
    enable : bpy.props.BoolProperty(default=True, options={'HIDDEN'})
    disable : bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    
    @classmethod
    def poll(self, context):
        return (context.active_object and context.selected_objects)
    
    @classmethod
    def description(cls, context, properties):
        if properties.enable and not properties.disable:
            return 'Enable this constraint. Sets influence to 1'
        elif not properties.enable and not properties.disable:
            return 'Disable this constraint. Sets influence to 0'
        else:
            return 'Disable all constraints. Sets all influences to 0'

    def execute(self, context):
        
        snap = context.scene.my_tool.snap_checkbox
        name = context.scene.my_tool.name_checkbox
        link = context.scene.my_tool.link_checkbox
        
        obj = context.active_object
        matrix = get_matrix(context, obj)
        obj = get_obj(context)
        
        for con in obj.constraints:
            if valid_constraint(con):
                if name:
                    namecheck = con.name
                elif con.subtarget:
                    namecheck = con.subtarget
                else:
                    namecheck = con.target.name

                if self.item and (link or (namecheck == self.item)):
                    change_influence(con, namecheck, self.item, self.enable)
                elif self.disable:
                    change_influence(con, namecheck, namecheck, False)
        
        bpy.context.view_layer.update()
        
        if snap:
            object = context.active_object
            armature = ''
            bone = False
            if object_in_posemode(object):
                bone = True
                armature = object
                object = context.active_pose_bone 
            matrices = calculate_childof(object)        
            apply_snap(matrices, matrix, object, armature, bone)
        
        if bpy.context.scene.tool_settings.use_keyframe_insert_auto == True:
            bpy.ops.absolutesnap.keyconstraint(item=self.item)
            
        return {'FINISHED'}

class KEY_CONSTRAINT(bpy.types.Operator):
    bl_idname = "absolutesnap.keyconstraint"
    bl_description = "Set key"
    bl_label = ""
    bl_options = {"REGISTER", "UNDO"}
    
    item : bpy.props.StringProperty(default='', options={'HIDDEN'})
    
    @classmethod
    def poll(self, context):
        return (context.active_object and context.selected_objects)

    def execute(self, context):
        
        name = context.scene.my_tool.name_checkbox
        obj = get_obj(context)
        
        if bpy.context.scene.tool_settings.use_keyframe_insert_auto == True:
            key_object(obj)
        
        for con in obj.constraints:
            if valid_constraint(con):
                if name:
                    namecheck = con.name
                elif con.subtarget:
                    namecheck = con.subtarget
                else:
                    namecheck = con.target.name
                if namecheck == self.item:
                    con.keyframe_insert(data_path="influence")
                    
        refresh_anim()
                
        return {'FINISHED'}
    
class UNKEY_CONSTRAINT(bpy.types.Operator):
    bl_idname = "absolutesnap.unkeyconstraint"
    bl_description = "Remove key"
    bl_label = ""
    bl_options = {"REGISTER", "UNDO"}
    
    item : bpy.props.StringProperty(default='', options={'HIDDEN'})
    
    @classmethod
    def poll(self, context):
        return (context.active_object and context.selected_objects)

    def execute(self, context):
        
        name = context.scene.my_tool.name_checkbox
        
        obj = get_obj(context)
        
        for con in obj.constraints:
            if valid_constraint(con):
                if name:
                    namecheck = con.name
                elif con.subtarget:
                    namecheck = con.subtarget
                else:
                    namecheck = con.target.name
                if not self.item or (self.item and (namecheck == self.item)):
                    con.keyframe_delete(data_path="influence")
                
        refresh_anim()
                
        return {'FINISHED'}
    
class KEY_ALL(bpy.types.Operator):
    bl_idname = "absolutesnap.keyall"
    bl_description = "Set keys on the current frame for the active selection and all constraints"
    bl_label = "Key All"
    bl_options = {"REGISTER", "UNDO"}
    
    @classmethod
    def poll(self, context):
        return (context.active_object and context.selected_objects)

    def execute(self, context):
        
        obj = get_obj(context)
        key_object(obj)
        
        for con in obj.constraints:
            if valid_constraint(con):
                con.keyframe_insert(data_path="influence")
                
        refresh_anim()  
                
        return {'FINISHED'}
    
class UNKEY_ALL(bpy.types.Operator):
    bl_idname = "absolutesnap.unkeyall"
    bl_description = "Delete keys on the current frame for the active selection and all constraints.\nMust have at least one constraint keyed"
    bl_label = ""
    bl_options = {"REGISTER", "UNDO"}
    
    @classmethod
    def poll(self, context):
        
        valid = False
        obj = get_obj(context)
        fcurve_prefix = ''
        if context.selected_pose_bones and context.active_pose_bone:
            fcurve_prefix = f'pose.bones["{obj.name}"].' 
        for con in obj.constraints:
            if valid_constraint(con):
                fcurve_name = f'{fcurve_prefix}constraints["{con.name}"].influence'
                if con.id_data.animation_data:
                    frame = bpy.context.scene.frame_current
                    action = con.id_data.animation_data.action
                    if action is not None:
                        fcurves = get_action_fcurves(action) #wzrd: instead of fcurves = con.id_data.animation_data.action.fcurves
                        for fcurve in fcurves:
                            if fcurve.data_path == fcurve_name:
                                for keyframe in fcurve.keyframe_points:
                                    if keyframe.co[0] == frame:
                                        valid = True
        return valid

    def execute(self, context):
        
        obj = get_obj(context)
        unkey_object(obj)
        
        for con in obj.constraints:
            if valid_constraint(con):
                con.keyframe_delete(data_path="influence")
                
        refresh_anim()     
         
        return {'FINISHED'}

classes = (NB_Absolute_Snap_ui, 
            NB_Absolute_Snap_lcmenu,
            CON_LCMENU,
            KEY_ALL, 
            UNKEY_ALL,
            KEY_CONSTRAINT,
            UNKEY_CONSTRAINT, 
            TOGGLE_CONSTRAINT, 
            COPY_XFORM, 
            PASTE_XFORM, 
            SNAP_SELECTED,
            COPY_RELATIVE,
            PASTE_RELATIVE)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.utils.register_class(NBASProperties)
    bpy.types.Scene.my_tool = bpy.props.PointerProperty(type=NBASProperties)

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)   
    del bpy.types.Scene.my_tool

if __name__ == "__main__":
    register()