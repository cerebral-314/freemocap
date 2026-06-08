from pyqtgraph.parametertree import Parameter
from skellytracker.trackers.mediapipe_tracker.mediapipe_model_info import (
    MediapipeTrackingParams,
)

from freemocap.data_layer.recording_models.post_processing_parameter_models import (
    ProcessingParameterModel,
    AniposeTriangulate3DParametersModel,
    PostProcessingParametersModel,
    ButterworthFilterParametersModel,
    DepthFusionParametersModel,
)

BUTTERWORTH_ORDER = "Order"

BUTTERWORTH_CUTOFF_FREQUENCY = "Cutoff Frequency"

POST_PROCESSING_FRAME_RATE = "Framerate"

BUTTERWORTH_FILTER_TREE_NAME = "Butterworth Filter"

OUTLIER_REJECTION_TREE_NAME = "Outlier Rejection"

USE_OUTLIER_REJECTION_METHOD = "Use Outlier Rejection Method?"

OUTLIER_REJECTION_MAXIMUM_CAMERAS_TO_DROP = "Maximum Cameras to Drop"

OUTLIER_REJECTION_MINIMUM_CAMERAS_FOR_TRIANGULATION = "Minimum Cameras for Triangulation"

OUTLIER_REJECTION_TARGET_REPROJECTION_ERROR = "Target Reprojection Error"

ANIPOSE_CONFIDENCE_CUTOFF = "Confidence Threshold Cut-off"

FLATTEN_SINGLE_CAMERA_DATA = "Flatten Single Camera Data (Recommended)"

DEPTH_FUSION_TREE_NAME = "RGB-D Depth Fusion"

USE_DEPTH_FUSION = "Use RGB-D Depth Fusion?"

DEPTH_WEIGHT = "Depth Weight"

MAX_DEPTH_JOINT_DISTANCE_M = "Max Depth/Joint Distance (m)"

DEPTH_PATCH_RADIUS_PX = "Depth Patch Radius (px)"

MIN_VALID_DEPTH_PIXELS = "Min Valid Depth Pixels"

REJECT_DEPTH_EDGES = "Reject Depth Edges"

USE_DEPTH_FOR_OCCLUSION_REASONING = "Use Depth for Occlusion Reasoning"

SAVE_RGBD_DIAGNOSTICS = "Save RGB-D Diagnostics"

REPLACE_TRIANGULATED_WITH_REFINED = "Replace Triangulated Skeleton with RGB-D Refined Skeleton"

ANIPOSE_TREE_NAME = "Anipose Triangulation"

YOLO_CROP_TREE_NAME = "YOLO Crop"

USE_YOLO_CROP_METHOD = "Use YOLO Crop Method"

YOLO_MODEL_SIZE = "YOLO Model Size"

BOUNDING_BOX_BUFFER_METHOD = "Buffer Bounding Box:"

BOUNDING_BOX_BUFFER_PERCENTAGE = "Bounding Box Buffer Percentage"

STATIC_IMAGE_MODE = "Static Image Mode"

MINIUMUM_TRACKING_CONFIDENCE = "Minimum Tracking Confidence"

MINIMUM_DETECTION_CONFIDENCE = "Minimum Detection Confidence"

MEDIAPIPE_MODEL_COMPLEXITY = "Model Complexity"

MEDIAPIPE_TREE_NAME = "Mediapipe"

RUN_IMAGE_TRACKING_NAME = "Run 2d image tracking?"

RUN_3D_TRIANGULATION_NAME = "Run 3d triangulation?"

RUN_BUTTERWORTH_FILTER_NAME = "Run butterworth filter?"

NUMBER_OF_PROCESSES_PARAMETER_NAME = "Max Number of Processes to Use"


# TODO: figure out how to generalize this
def create_mediapipe_parameter_group(
        parameter_model: MediapipeTrackingParams,
) -> Parameter:
    mediapipe_model_complexity_list = [
        "0 (Fastest/Least accurate)",
        "1 (Middle ground)",
        "2 (Slowest/Most accurate)",
    ]
    return Parameter.create(
        name=MEDIAPIPE_TREE_NAME,
        type="group",
        expanded=False, # collapsed by default

        children=[

            dict(
                name=MEDIAPIPE_MODEL_COMPLEXITY,
                type="list",
                limits=mediapipe_model_complexity_list,
                value=mediapipe_model_complexity_list[parameter_model.mediapipe_model_complexity],
                tip="Which Mediapipe model to use - higher complexity is slower but more accurate. "
                    "Variable name in `mediapipe` code: `mediapipe_model_complexity`",
            ),
            dict(
                name=MINIMUM_DETECTION_CONFIDENCE,
                type="float",
                value=parameter_model.min_detection_confidence,
                step=0.05,
                limits=(0.0, 1.0),
                tip="Minimum confidence for a skeleton detection to be considered valid. "
                    "Variable name in `mediapipe` code: `min_detection_confidence`."
                    "NOTE - Never trust a machine learning model's estimates of their own confidence!",
            ),
            dict(
                name=MINIUMUM_TRACKING_CONFIDENCE,
                type="float",
                value=parameter_model.min_tracking_confidence,
                step=0.05,
                limits=(0.0, 1.0),
                tip="Minimum confidence needed to use the previous frame's skeleton estiamte to predict the next one"
                    "Variable name in `mediapipe` code: `min_tracking_confidence`.",
            ),
            dict(
                name=STATIC_IMAGE_MODE,
                type="bool",
                value=parameter_model.static_image_mode,
                tip="If true, the model will process each image independently, without tracking across frames."
                    "I think this is equivalent to setting `min_tracking_confidence` to 0.0"
                    "Variable name in `mediapipe` code: `static_image_mode`",
            ),
            dict(
                name=YOLO_CROP_TREE_NAME,
                type="group",
                expanded=False, # collapsed by default
                children=[
                    dict(
                        name=USE_YOLO_CROP_METHOD,
                        type="bool",
                        value=parameter_model.use_yolo_crop_method,
                        tip="If true, `skellytracker` will use YOLO to pre-crop the person from the image before running the `mediapipe` tracker",
                    ),
                    dict(
                        name=YOLO_MODEL_SIZE,
                        type="list",
                        limits=["nano", "small", "medium", "large", "extra_large", "high_res"],
                        value=parameter_model.yolo_model_size,
                        tip="Smaller models are faster but may be less accurate",
                    ),
                    dict(
                        name=BOUNDING_BOX_BUFFER_METHOD,
                        type="list",
                        limits=["By box size", "By image size"],
                        value=parameter_model.buffer_size_method,
                        tip="Buffer bounding box by percentage of either box size or image size",
                    ),
                    dict(
                        name=BOUNDING_BOX_BUFFER_PERCENTAGE,
                        type="int",
                        value=parameter_model.bounding_box_buffer_percentage,
                        limits=(0, 100),
                        step=1,
                        tip="Percentage to increase size of bounding box",
                    ),
                ],
            ),
        ],
    )


# def create_3d_triangulation_parameter_group(
#         parameter_model: AniposeTriangulate3DParametersModel = None,
# ) -> Parameter:
#     if parameter_model is None:
#         parameter_model = AniposeTriangulate3DParametersModel()
#
#     return Parameter.create(
#         name=ANIPOSE_TREE_NAME,
#         type="group",
#         children=[
#
#             dict(
#                 name=FLATTEN_SINGLE_CAMERA_DATA,
#                 type="bool",
#                 value=parameter_model.flatten_single_camera_data,
#                 tip="If true, flatten the data from single camera recordings.",
#             ),
#             dict(
#                 name=OUTLIER_REJECTION_TREE_NAME,
#                 type="group",
#                 children=[
#                     dict(
#                         name=USE_OUTLIER_REJECTION_METHOD,
#                         type="bool",
#                         value=parameter_model.use_triangulate_outlier_rejection,
#                         tip="If true, use `anipose`'s `triangulate_using_outlier_rejection` method.",
#                     ),
#                     dict(
#                         name=OUTLIER_REJECTION_MAXIMUM_CAMERAS_TO_DROP,
#                         type="int",
#                         value=parameter_model.maximum_cameras_to_drop,
#                         limits=(0, 100),
#                         step=1,
#                         tip="Maximum amount of cameras permitted to drop.",
#                     ),
#                     dict(
#                         name=OUTLIER_REJECTION_TARGET_REPROJECTION_ERROR,
#                         type="float",
#                         value=parameter_model.target_reprojection_error,
#                         limits=(0.0, 1.0),
#                         step=0.001,
#                         tip="The target reprojection error that stops the outlier rejection search.\n"
#                             "If a camera combination achieves an error below this value, it is accepted and further dropped-camera iterations are skipped.",
#                     ),
#                 ],
#             ),
#         ],
#     )


def create_post_processing_parameter_group(
        parameter_model: PostProcessingParametersModel = None,
) -> Parameter:
    if parameter_model is None:
        parameter_model = PostProcessingParametersModel()

    return Parameter.create(
        name=BUTTERWORTH_FILTER_TREE_NAME,
        type="group",
        expanded=False, # collapsed by default
        children=[
            dict(
                name=POST_PROCESSING_FRAME_RATE,
                type="float",
                value=parameter_model.butterworth_filter_parameters.sampling_rate,
                tip="Framerate of the recording " "TODO - Calculate this from the recorded timestamps....",
            ),
            dict(
                name=BUTTERWORTH_CUTOFF_FREQUENCY,
                type="float",
                value=parameter_model.butterworth_filter_parameters.cutoff_frequency,
                tip="Oscillations above this frequency will be filtered from the data. ",
            ),
            dict(
                name=BUTTERWORTH_ORDER,
                type="int",
                value=parameter_model.butterworth_filter_parameters.order,
                tip="Order of the filter."
                    "NOTE - I'm not really sure what this parameter does, but this is what I see in other people's Methods sections so....   lol",
            ),
        ],
        tip="Low-pass, zero-lag, Butterworth filter to remove high frequency oscillations/noise from the data. ",
    )


def create_depth_fusion_parameter_group(
        parameter_model: DepthFusionParametersModel = None,
) -> Parameter:
    if parameter_model is None:
        parameter_model = DepthFusionParametersModel()

    return Parameter.create(
        name=DEPTH_FUSION_TREE_NAME,
        type="group",
        expanded=False,
        children=[
            dict(
                name=USE_DEPTH_FUSION,
                type="bool",
                value=parameter_model.use_depth_fusion,
                tip="Use output_data/raw_data/rgbd_depth_observations.npz to refine normal triangulated 3D data.",
            ),
            dict(
                name=DEPTH_WEIGHT,
                type="float",
                value=parameter_model.depth_weight,
                limits=(0.0, 100.0),
                step=0.1,
                tip="Relative weight for accepted LiDAR/depth observations during RGB-D fusion.",
            ),
            dict(
                name=MAX_DEPTH_JOINT_DISTANCE_M,
                type="float",
                value=parameter_model.max_depth_joint_distance_m,
                limits=(0.0, 10.0),
                step=0.01,
                tip="Reject a depth observation if it is farther than this from the triangulated joint.",
            ),
            dict(
                name=DEPTH_PATCH_RADIUS_PX,
                type="int",
                value=parameter_model.depth_patch_radius_px,
                limits=(0, 50),
                step=1,
                tip="Radius of the depth patch sampled around each 2D landmark.",
            ),
            dict(
                name=MIN_VALID_DEPTH_PIXELS,
                type="int",
                value=parameter_model.min_valid_depth_pixels,
                limits=(1, 1000),
                step=1,
                tip="Minimum valid pixels required in the sampled depth patch.",
            ),
            dict(
                name=REJECT_DEPTH_EDGES,
                type="bool",
                value=parameter_model.reject_depth_edges,
                tip="Reserved for edge-aware depth rejection in the RGB-D importer.",
            ),
            dict(
                name=USE_DEPTH_FOR_OCCLUSION_REASONING,
                type="bool",
                value=parameter_model.use_depth_for_occlusion_reasoning,
                tip="Reserved for depth-based view confidence and occlusion scoring.",
            ),
            dict(
                name=SAVE_RGBD_DIAGNOSTICS,
                type="bool",
                value=parameter_model.save_rgbd_diagnostics,
                tip="Save accepted/rejected depth point diagnostics next to the refined 3D data.",
            ),
            dict(
                name=REPLACE_TRIANGULATED_WITH_REFINED,
                type="bool",
                value=parameter_model.replace_triangulated_with_refined,
                tip="Use the RGB-D refined skeleton as the 3D output passed to later processing steps.",
            ),
        ],
        tip="Depth-aware refinement for MocapCam RGB-D recordings.",
    )


def extract_parameter_model_from_parameter_tree(
        parameter_object: Parameter,
) -> ProcessingParameterModel:
    parameter_values_dictionary = extract_processing_parameter_model_from_tree(parameter_object=parameter_object)

    return ProcessingParameterModel(
        tracking_parameters_model=MediapipeTrackingParams(
            mediapipe_model_complexity=get_integer_from_mediapipe_model_complexity(
                parameter_values_dictionary[MEDIAPIPE_MODEL_COMPLEXITY]
            ),
            min_detection_confidence=parameter_values_dictionary[MINIMUM_DETECTION_CONFIDENCE],
            min_tracking_confidence=parameter_values_dictionary[MINIUMUM_TRACKING_CONFIDENCE],
            static_image_mode=parameter_values_dictionary[STATIC_IMAGE_MODE],
            run_image_tracking=parameter_values_dictionary[RUN_IMAGE_TRACKING_NAME],
            num_processes=parameter_values_dictionary[NUMBER_OF_PROCESSES_PARAMETER_NAME],
            use_yolo_crop_method=parameter_values_dictionary[USE_YOLO_CROP_METHOD],
            yolo_model_size=parameter_values_dictionary[YOLO_MODEL_SIZE],
            buffer_size_method=get_bounding_box_buffer_method_from_string(
                parameter_values_dictionary[BOUNDING_BOX_BUFFER_METHOD]
            ),
            bounding_box_buffer_percentage=parameter_values_dictionary[BOUNDING_BOX_BUFFER_PERCENTAGE],
        ),
        anipose_triangulate_3d_parameters_model=AniposeTriangulate3DParametersModel(
            flatten_single_camera_data=parameter_values_dictionary[FLATTEN_SINGLE_CAMERA_DATA],
            use_triangulate_outlier_rejection=parameter_values_dictionary[USE_OUTLIER_REJECTION_METHOD],
            minimum_cameras_for_triangulation=parameter_values_dictionary[OUTLIER_REJECTION_MINIMUM_CAMERAS_FOR_TRIANGULATION],
            maximum_cameras_to_drop=parameter_values_dictionary[OUTLIER_REJECTION_MAXIMUM_CAMERAS_TO_DROP],
            target_reprojection_error=parameter_values_dictionary[OUTLIER_REJECTION_TARGET_REPROJECTION_ERROR],
            run_3d_triangulation=parameter_values_dictionary[RUN_3D_TRIANGULATION_NAME],
        ),
        depth_fusion_parameters_model=DepthFusionParametersModel(
            use_depth_fusion=parameter_values_dictionary[USE_DEPTH_FUSION],
            depth_weight=parameter_values_dictionary[DEPTH_WEIGHT],
            max_depth_joint_distance_m=parameter_values_dictionary[MAX_DEPTH_JOINT_DISTANCE_M],
            depth_patch_radius_px=parameter_values_dictionary[DEPTH_PATCH_RADIUS_PX],
            min_valid_depth_pixels=parameter_values_dictionary[MIN_VALID_DEPTH_PIXELS],
            reject_depth_edges=parameter_values_dictionary[REJECT_DEPTH_EDGES],
            use_depth_for_occlusion_reasoning=parameter_values_dictionary[USE_DEPTH_FOR_OCCLUSION_REASONING],
            save_rgbd_diagnostics=parameter_values_dictionary[SAVE_RGBD_DIAGNOSTICS],
            replace_triangulated_with_refined=parameter_values_dictionary[REPLACE_TRIANGULATED_WITH_REFINED],
        ),
        post_processing_parameters_model=PostProcessingParametersModel(
            framerate=parameter_values_dictionary[POST_PROCESSING_FRAME_RATE],
            butterworth_filter_parameters=ButterworthFilterParametersModel(
                sampling_rate=parameter_values_dictionary[POST_PROCESSING_FRAME_RATE],
                cutoff_frequency=parameter_values_dictionary[BUTTERWORTH_CUTOFF_FREQUENCY],
                order=parameter_values_dictionary[BUTTERWORTH_ORDER],
            ),
            run_butterworth_filter=parameter_values_dictionary[RUN_BUTTERWORTH_FILTER_NAME],
        ),
    )


def get_integer_from_mediapipe_model_complexity(mediapipe_model_complexity_value: str):
    mediapipe_model_complexity_dictionary = {
        "0 (Fastest/Least accurate)": 0,
        "1 (Middle ground)": 1,
        "2 (Slowest/Most accurate)": 2,
    }
    return mediapipe_model_complexity_dictionary[mediapipe_model_complexity_value]


def get_bounding_box_buffer_method_from_string(buffer_method_string: str) -> str:
    bounding_box_buffer_method_dict = {
        "By box size": "buffer_by_box_size",
        "By image size": "buffer_by_image_size",
    }
    return bounding_box_buffer_method_dict[buffer_method_string]


def extract_processing_parameter_model_from_tree(parameter_object, value_dictionary: dict = None):
    if value_dictionary is None:
        value_dictionary = {}

    for child in parameter_object.children():
        if child.hasChildren():
            extract_processing_parameter_model_from_tree(child, value_dictionary)
        else:
            value_dictionary[child.name()] = child.value()
    return value_dictionary
