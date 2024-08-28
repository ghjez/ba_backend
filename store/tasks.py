"""
for the celery tasks

"""

# tasks.py
from celery import shared_task, chain
from .utility.ai_utils import prepare_cfg, run_ai_model
from .models import Image, ResultSet, Project, ChainModuleResult, ChainModuleResultSet, AiChainModule
from django.conf import settings
import os
import requests
import json
from zipfile import ZipInfo, ZipFile


@shared_task
def process_file_in_module(parameters, module_url):
    project_id, image_id, chain_result_set_id, input_filepath = parameters

    image = Image.objects.get(id=image_id)
    project = Project.objects.get(id=project_id)
    chain_result_set = ChainModuleResultSet.objects.get(id=chain_result_set_id)
    module = AiChainModule.objects.get(module_url=module_url)

    with open(input_filepath, 'rb') as f:
        files = {'file': f}
        response = requests.post(module_url, files=files)

    if response.status_code != 200:
        raise Exception(f"Stage failed with status code {response.status_code}")
    
    result_zip = response.content

    temp_zip_path = os.path.join(settings.MEDIA_ROOT, 'outputs', 'temp_results.zip')
    with open(temp_zip_path, 'wb') as f:
        f.write(result_zip)

    jsons = []

    with ZipFile(temp_zip_path) as zObject:
        filelist = zObject.infolist()

        # gather images
        filenames = [zInfo for zInfo in filelist
                    if zInfo.filename.endswith(".json")]

        for filename in filenames:
            with zObject.open(filename.filename) as myJson:
                jsons.append(json.loads(myJson.read()))

    if len(jsons) == 0: 
        jsons[0] = json.dumps({'msg':'No .json result was produced'})

    module_result = ChainModuleResult.objects.create(
        project = project,
        module = module,
        # image = image, # DO NOT set an image, unless you want the project to have the COMPLETE status before the chain actually comes to completion

        # Since for now each module produces only one .json result file 
        # and the currently used frontend expects one .json file per moule 
        # only the first (and most probably the only one) .json file is saved to database.
        result = jsons[0],
        result_set = chain_result_set
    )

    # os.remove(temp_zip_path)
    return project_id, image_id, chain_result_set_id, temp_zip_path

@shared_task
def processing_chain(project_id, image_id, ai_chain_modules_list):

    #stages = [
    #   "http://127.0.0.1:5001/localize",
    #   "http://127.0.0.1:5002/recognize"
    #]

    stages = []
    for module_id in ai_chain_modules_list:
        module = AiChainModule.objects.get(id=module_id)
        stages.append(module.module_url)
    
    image = Image.objects.get(id=image_id)
    project = Project.objects.get(id=project_id)
    image_name = image.name  # the image_name here is with extensions
    image_old_name = image.old_name
    image_file_path = image.image_url()

    
    image_local_filepath = image.image_local_path()

    chain_result_set = ChainModuleResultSet.objects.create(
        project = project,
        image_id = image_id
    )

    chain_result_set_id = chain_result_set.id

    tasks = []
    for i, url in enumerate(stages):
        if i == 0:
            parameters = project_id, image_id, chain_result_set_id, image_local_filepath
            task = process_file_in_module.s(parameters, url)
        else:
            task = process_file_in_module.s(url)
        tasks.append(task)
    
    final_task_task = final_task.s()
    tasks.append(final_task_task)

    pipeline = chain(tasks)()

    return pipeline

@shared_task
def final_task(parameters):
    project_id, image_id, chain_result_set_id, input_filepath = parameters
    image = Image.objects.get(id=image_id)
    project = Project.objects.get(id=project_id)
    image_name = image.name  # the image_name here is with extensions
    image_old_name = image.old_name
    image_file_path = image.image_url()

    if input_filepath is not None:
        success = True
    else:
        success = False

    result_data = {
        "image_id": image_id,
        "image_info": {
            "name": image_name,
            "old_name": image_old_name,
            "image_url": image_file_path
        },
        "success": success,
        "error_msg": ""
    }
    chain_result_set = ChainModuleResultSet.objects.get(id=chain_result_set_id)
    chain_result_set.update_image_status()
    
    return result_data



@shared_task
def process_image(project_id, image_id, ai_model_id):
    # Retrieve the image instance
    image = Image.objects.get(id=image_id)
    project = Project.objects.get(id=project_id)
    image_name = image.name  # the image_name here is with extensions
    image_old_name = image.old_name
    image_file_path = image.image_url()
    image_base_name = os.path.splitext(image_name)[0]

    cfg_file_path = prepare_cfg(project_id, image_name, ai_model_id)
    # the ai result will be a boolean, True
    ai_processing_successful = run_ai_model(cfg_file_path)

    # Ergebnis-Dictionary vorbereiten
    result_data = {
        "image_id": image_id,
        "image_info": {
            "name": image_name,
            "old_name": image_old_name,
            "image_url": image_file_path
        },
        "success": ai_processing_successful,
        "error_msg": ""
    }

    if ai_processing_successful:
        base_output_path_relative = os.path.join('outputs', f'project_{project_id}', image_base_name)
        detection_image_relative_path = os.path.join(base_output_path_relative, 'text_detection', 'final', 'visual', image_name)
        recognition_image_relative_path = os.path.join(base_output_path_relative, 'text_recognition', 'final', 'visual', image_name)
        interpretation_image_relative_path = os.path.join(base_output_path_relative, 'text_interpretation', 'final', 'visual', image_name)
        # Construct paths for the processed JSON results
        base_output_path = os.path.join(settings.MEDIA_ROOT, 'outputs', f'project_{project_id}', image_base_name)
        # Load JSON results
        detection_json_path = os.path.join(base_output_path, 'text_detection', 'final', 'results.json')
        recognition_json_path = os.path.join(base_output_path, 'text_recognition', 'final', 'results.json')
        interpretation_json_path = os.path.join(base_output_path, 'text_interpretation', 'final', 'floor.json')

        # Check if all 3 JSON files exist, if all exists then means the processing is done[actually if the interpretation_json_path exists, then it means it is done]
        if os.path.exists(detection_json_path) and os.path.exists(recognition_json_path) and os.path.exists(interpretation_json_path):
            with open(detection_json_path, 'r') as file:
                detection_result = json.load(file)
            with open(recognition_json_path, 'r') as file:
                recognition_result = json.load(file)
            with open(interpretation_json_path, 'r') as file:
                interpretation_result = json.load(file)

            # check if the result with the image_id already exists or not, if yes, then update the old tuple:

            result_set, created = ResultSet.objects.update_or_create(
                image_id=image_id,
                defaults={
                    "project_id": project_id,
                    "ai_model_id": ai_model_id,
                    "result_detection": detection_result,
                    "result_recognition": recognition_result,
                    "result_interpretation": interpretation_result,
                    "text_detection_image_path": detection_image_relative_path,
                    "text_recognition_image_path": recognition_image_relative_path,
                    "text_interpretation_image_path": interpretation_image_relative_path,
                }
            )

        else:
            result_data["error_msg"] = f"The singe processing succeeds for image with name: {image_name}, in project with project id: {project_id}, but not all 3 json files are produced"
            result_data["success"] = False
    else:
        # set the status of project to Failed
        project.status = Project.STATUS_CHOICES[3][0]  # 'FAILED'
        project.save()
        result_data["error_msg"] = f"The singe processing failed for image with name: {image_name}, in project with project id: {project_id}, this image can not be processed, please delete this image of this project and upload a new one and try again"
        result_data["success"] = False

    return result_data




# currently not in use
@shared_task
def update_project_status(project_id):
    project = Project.objects.get(id=project_id)
    # update the project status
    project.update_status_based_on_images()