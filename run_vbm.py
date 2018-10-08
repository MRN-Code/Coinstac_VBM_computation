#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This layer includes the interface adapter(IA) for parsing json args to read ni pre-processing structural T1w scans (accepts BIDS format)
This layer sends the output to vbm_use_cases_layer with the appropriate inputs to run the pipeine using nipype interface

Sample run for bids input data:
python3 run_fmri.py '{"input":{"opts":{"fwhm": 7}, "data":"/computation/test_dir/bids_input_data"}}'

Sample run for input data of nifti paths in text or csv file:
python3 run_fmri.py '{"input":{"opts":{"fwhm": 7}, "NiftiPaths":"/computation/test_dir/nifti_paths.txt"}}'

success=True means program finished execution , despite the success or failure of the code
This is to indicate to coinstac that program finished execution
"""
import contextlib


@contextlib.contextmanager
def stdchannel_redirected(stdchannel, dest_filename):
    """
    A context manager to temporarily redirect stdout or stderr

    e.g.:


    with stdchannel_redirected(sys.stderr, os.devnull):
        if compiler.has_function('clock_gettime', libraries=['rt']):
            libraries.append('rt')
    """

    try:
        oldstdchannel = os.dup(stdchannel.fileno())
        dest_file = open(dest_filename, 'w')
        os.dup2(dest_file.fileno(), stdchannel.fileno())

        yield
    finally:
        if oldstdchannel is not None:
            os.dup2(oldstdchannel, stdchannel.fileno())
        if dest_file is not None:
            dest_file.close()


import ujson as json
import warnings, os, glob, sys, shutil
import nibabel as nib

from bids.grabbids import BIDSLayout

## Load Nipype spm interface ##
from nipype.interfaces import spm

#Stop printing nipype.workflow info to stdout
from nipype import logging
logging.getLogger('nipype.workflow').setLevel('CRITICAL')

import vbm_use_cases_layer

#Create a dict to store all paths to softwares,templates & store parameters, names of output files

template_dict = {
    'spm_version':
    '12.7169',
    'matlab_cmd':
    '/opt/spm12/run_spm12.sh /opt/mcr/v92 script',
    'spm_path':
    '/opt/spm12/fsroot',
    'tpm_path':
    '/opt/spm12/fsroot/spm/spm12/tpm/TPM.nii',
    'transf_mat_path':
    os.path.join('/computation', 'transform.mat'),
    'scan_type':
    'T1w',
    'FWHM_SMOOTH': [10, 10, 10],
    'BIAS_REGULARISATION':
    0.0001,
    'FWHM_GAUSSIAN_SMOOTH_BIAS':
    60,
    'vbm_output_dirname':
    'vbm_spm12',
    'output_zip_dir':
    'vbm_outputs',
    'display_image_name':
    'wc1Re.png',
    'display_pngimage_name':
    'Gray matter (Normalized)',
    'cut_coords': (0, 0, 0),
    'display_nifti':
    'wc1Re.nii',
    'qc_nifti':
    'swc1*nii',
    'vbm_qc_filename':
    'vbm_corr_value.txt',
    'bids_outputs_manual_name':
    'outputs_description.txt',
    'nifti_outputs_manual_name':
    'outputs_description.txt',
    'bids_outputs_manual_content':
    "Prefixes descriptions for segmented images:c1-Gray matter,c2-White matter,c3-Cerebro spinal fluid,c4-Bone,c5-Soft tissue,c6-Air(background)"
    "\nw-Normalized\nm-Modulated\ns-Smoothed with fwhm(mm) [10 10 10]\nFor more info. please refer to spm12 manual here: "
    "http://www.fil.ion.ucl.ac.uk/spm/doc/manual.pdf and release notes here: http://www.fil.ion.ucl.ac.uk/spm/software/spm12/SPM12_Release_Notes.pdf",
    'nifti_outputs_manual_content':
    "sub-1,sub-2,sub-* denotes each nifti file with respect to the order in the nifti paths given"
    "Prefixes descriptions for segmented images:c1-Grey matter,c2-White matter,c3-Cerebro spinal fluid,c4-Bone,c5-Soft tissue,c6-Air(background)"
    "\nw-Normalized\nm-Modulated\ns-Smoothed with fwhm(mm) [10 10 10]\nFor more info. please refer to spm12 manual here: "
    "http://www.fil.ion.ucl.ac.uk/spm/doc/manual.pdf and release notes here: http://www.fil.ion.ucl.ac.uk/spm/software/spm12/SPM12_Release_Notes.pdf",
    'qc_readme_name':
    'quality_control_readme.txt',
    'qc_readme_content':
    "In each subject's anat/vbm_spm12 directory,vbm_corr_value.txt gives the correlation value of the swc1*nii file with spm12 template (TPM.nii) file from SPM12 toolbox"
}
"""
More info. on keys in template_dict

spm_path is path to spm software inside docker . 
SPM is Statistical Parametric Mapping toolbox for matlab 
Info. from http://www.fil.ion.ucl.ac.uk/spm/
"Statistical Parametric Mapping refers to the construction and assessment of spatially extended statistical processes used to test hypotheses about functional imaging data. 
These ideas have been instantiated in software that is called SPM.
The SPM software package has been designed for the analysis of brain imaging data sequences. 
The sequences can be a series of images from different cohorts, or time-series from the same subject. 
The current release is designed for the analysis of fMRI, PET, SPECT, EEG and MEG."

tpm_path is the path where the SPM structural template nifti file is stored
This file is used to :
1) Perform segmentation in the VBM pipeline
2) Compute correlation value to smoothed, warped grey matter from output of pipeline, which is stored in the vbm_qc_filename

transf_mat_path is the path to the transformation matrix used in running the reorient step of the pipeline
scan_type is the type of structural scans on which is accepted by this pipeline
FWHM_SMOOTH is the full width half maximum smoothing kernel value in mm in x,y,z directions
vbm_output_dirname is the name of the output directory to which the outputs from this pipeline are written to
vbm_qc_filename is the name of the VBM quality control text file , which is placed in vbm_output_dirname

For nifti files , it is assumed that they are T1w (T1 weighted) type of scans
FWHM_SMOOTH is an optional parameter that can be passed as json in args['input']['opts']

json output description
                    "message"-This string is used by coinstac to display output message to the user on the UI after computation is finished
                    "download_outputs"-Zipped directory where outputs are stored
                    "display"-base64 encoded string of segmented gray matter normalized output nifti
"""
with warnings.catch_warnings():
    warnings.filterwarnings("ignore")


def process_bids(args):
    """Runs the pre-processing pipeline on structural T1w scans in BIDS data
        Args:
            args (dictionary): {"input":{
                                        "options": {
                                                        "type": "integer",
                                                        "label": "Smoothing value in mm",
                                                        },
                                        "data": {
                                                        "type": "string",
                                                        "label": "Input Bids Directory",
                                                        }
                                        }
                                }
        Returns:
            computation_output (json): {"output": {
                                                  "success": {
                                                    "type": "boolean"
                                                  },
                                                   "message": {
                                                    "type": "string"
                                                  },
                                                  "download_outputs": {
                                                    "type": "string"
                                                  },
                                                   "display": {
                                                    "type": "string"
                                                  }
                                                  }
                                        }
        Comments:
            After verifying the BIDS format , the bids_dir along with pre-processing specific pipeline options
            are sent to vbm_use_cases_layer for running the pipeline

            The foll. args are used for reading from coinstac user directly, but for demo purposes we can read from args['state']['baseDirectory'] and args['state']['outputDirectory']
    """
    BidsDir = args['input']['data']
    WriteDir = args['state']['outputDirectory']

    if ('options' in args['input']) and (args['input']['options']):
        opts = args['input']['options']
    else:
        opts = None

    return vbm_use_cases_layer.execute_pipeline(
        bids_dir=BidsDir,
        write_dir=WriteDir,
        data_type='bids',
        pipeline_opts=opts,
        **template_dict)


def process_niftis(args):
    """Runs the pre-processing pipeline on structural T1w nifti scans from paths in the text or csv file
            Args:
                args (dictionary): {"input":{
                                            "NiftiFile": {
                                                            "type": "string",
                                                            "label": "text file with complete T1w nifti paths",
                                                            },
                                            "options": {
                                                            "type": "integer",
                                                            "label": "Smoothing value in mm",
                                                            }
                                            }
                                    }
            Returns:
            computation_output (json): {"output": {
                                                  "success": {
                                                    "type": "boolean"
                                                  },
                                                   "message": {
                                                    "type": "string"
                                                  },
                                                  "download_outputs": {
                                                    "type": "string"
                                                  },
                                                   "display": {
                                                    "type": "string"
                                                  }
                                                  }
                                        }
            Comments:
                After verifying the nifti paths , the paths to nifti files and write_dir along with pre-processing specific pipeline options
                are sent to vbm_use_cases_layer for running the pipeline

                The foll. args are used for reading from coinstac user directly, but for demo purposes we can read from args['state']['baseDirectory'] and args['state']['outputDirectory']
                paths_file = args['input']['NiftiPaths']
                WriteDir = args['input']['WriteDir']

            """
    #Get paths to *.csv or *.txt files
    nifti_paths_file = (
        glob.glob(os.path.join(args['state']['baseDirectory'], '*.csv'))
        or glob.glob(os.path.join(args['state']['baseDirectory'], '*.txt')))[0]
    WriteDir = args['state']['outputDirectory']

    if ('options' in args['input']) and (args['input']['options']):
        opts = args['input']['options']
    else:
        opts = None

    # Read each line in nifti_paths file into niftis variable
    count = 0
    valid_niftis = []

    with open(nifti_paths_file, "r") as f:
        for each in f:
            if nib.load(each.rstrip(('\n'))):
                valid_niftis.append(each.rstrip(('\n')))
                count += 1
    if count > 0 and os.access(WriteDir, os.W_OK):
        return vbm_use_cases_layer.execute_pipeline(
            nii_files=valid_niftis,
            write_dir=WriteDir,
            data_type='nifti',
            pipeline_opts=opts,
            **template_dict)
    else:
        return sys.stdout.write(
            json.dumps({
                "output": {
                    "message":
                    "No nifti files found or write directory does not have permissions"
                },
                "cache": {},
                "success": True
            }))


def software_check():
    """This function returns the spm standalone version installed inside the docker
    """
    spm.SPMCommand.set_mlab_paths(
        matlab_cmd=template_dict['matlab_cmd'], use_mcr=True)
    return (spm.SPMCommand().version)


if __name__ == '__main__':
    # Check if spm is running
    with stdchannel_redirected(sys.stderr, os.devnull):
        spm_check = software_check()
    if spm_check != template_dict['spm_version']:
        raise EnvironmentError("spm unable to start in vbm docker")

    # The following block of code assigns the appropriate pre-processing function for input data format, based on Bids or nifti file paths in text file
    args = json.loads(sys.stdin.read())

    BidsDir = args['input']['data']
    WriteDir = args['state']['outputDirectory']

    # Check if data is in BIDS format and has T1w
    layout = BIDSLayout(BidsDir)
    smri_data = layout.get(
        type=template_dict['scan_type'], extensions='.nii.gz')

    if (len(smri_data) > 0) and os.access(WriteDir, os.W_OK):
        computation_output = process_bids(args)
        sys.stdout.write(computation_output)
    elif (len(smri_data) == 0) and os.access(WriteDir, os.W_OK):
        computation_output = process_niftis(args)
        sys.stdout.write(computation_output)
    else:
        sys.stdout.write(
            json.dumps({
                "output": {
                    "message":
                    "Bids SMRI data not found or can not write to target directory"
                },
                "cache": {},
                "success": True
            }))
