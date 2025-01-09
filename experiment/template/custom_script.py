#!/usr/bin/env python3

import numpy as np
import logging
import os.path
import time
import pandas as pd
import traceback

import utils.step_utils as su
import utils.file_utils as fu
import utils.config_utils as cu
import step_control

# logger setup
logger = logging.getLogger(__name__)

##### USER DEFINED GENERAL SETTINGS #####

# If using the GUI for data visualization, do not change EXP_NAME!
# only change if you wish to have multiple data folders within a single
# directory for a set of scripts
EXP_NAME = 'data'
EXCEL_CONFIG_FILE = 'experiment_configurations.xlsx'

# Port for the eVOLVER connection. You should not need to change this unless you have multiple applications on a single RPi.
EVOLVER_PORT = 8081

##### Identify pump calibration files, define initial values for temperature, stirring, volume, power settings

GROWTH_CURVE_TIME = 0 # hours; experiment time after which to start turbidostat

TEMP_INITIAL = [37] * 16 #degrees C, makes 16-value list
#Alternatively enter 16-value list to set different values
# TEMP_INITIAL = [38,38,38,38,38,38,38,38,38,38,38,38,38,38,38,38]

STIR_INITIAL = [10]*16 #try 8,10,12 etc; makes 16-value list
#Alternatively enter 16-value list to set different values
#STIR_INITIAL = [7,7,7,7,8,8,8,8,9,9,9,9,10,10,10,10]

VOLUME =  25 #mL, determined by vial cap straw length
OPERATION_MODE = 'turbidostat' #use to choose between 'turbidostat' and 'chemostat' functions
# if using a different mode, name your function as the OPERATION_MODE variable


##### END OF USER DEFINED GENERAL SETTINGS #####


def turbidostat(eVOLVER, input_data, vials, elapsed_time):
    OD_data = input_data['transformed']['od']

    ##### USER DEFINED VARIABLES #####

    ### Turbidostat Settings ###
    turbidostat_vials = vials #vials is all 16, can set to different range (ex. [0,1,2,3]) to only trigger tstat on those vials
    stop_after_n_curves = np.inf #set to np.inf to never stop, or integer value to stop diluting after certain number of growth curves
    OD_values_to_average = 6  # Number of values to calculate the OD average
    
    if elapsed_time < GROWTH_CURVE_TIME:
        lower_thresh = [999] * 16  #to set all vials to the same value, creates 16-value list
        upper_thresh = [999] * 16 #to set all vials to the same value, creates 16-value list
    else: 
        lower_thresh = [1.6] * 16  #to set all vials to the same value, creates 16-value list
        upper_thresh = [2] * 16 #to set all vials to the same value, creates 16-value list

    if eVOLVER.experiment_params is not None:
        lower_thresh = list(map(lambda x: x['lower'], eVOLVER.experiment_params['vial_configuration']))
        upper_thresh = list(map(lambda x: x['upper'], eVOLVER.experiment_params['vial_configuration']))

    #Alternatively, use 16 value list to set different thresholds, use 9999 for vials not being used
    #lower_thresh = [0.2, 0.2, 0.3, 0.3, 9999, 9999, 9999, 9999, 9999, 9999, 9999, 9999, 9999, 9999, 9999, 9999]
    #upper_thresh = [0.4, 0.4, 0.4, 0.4, 9999, 9999, 9999, 9999, 9999, 9999, 9999, 9999, 9999, 9999, 9999, 9999]
    ### End of Turbidostat Settings ###

    ##### END OF USER DEFINED VARIABLES #####

    ##### ADVANCED SETTINGS #####
    ## Turbidostat Settings ##
    #Tunable settings for overflow protection, pump scheduling etc. Unlikely to change between expts
    time_out = 5 #(sec) additional amount of time to run efflux pump
    pump_wait = 20 # (min) minimum amount of time to wait between pump events
    ## End of Turbidostat Settings ##

    ## General Fluidics Settings ##
    flow_rate = eVOLVER.get_flow_rate() #read from calibration file
    bolus_fast = 0.5 #mL, can be changed with great caution, 0.2 is absolute minimum
    bolus_slow = 0.1 #mL, can be changed with great caution
    dilution_window = 3 # window on either side of a dilution to calculate dilution effect on OD TODO make a parameter in the experiment config file
    ## End of General Fluidics Settings ##
    
    ##### END OF ADVANCED SETTINGS #####

    ##### Turbidostat Control Code Below #####

    # fluidic message: initialized so that no change is sent
    MESSAGE = ['--'] * 48
    for x in turbidostat_vials: #main loop through each vial
        # Update turbidostat configuration files for each vial
        # initialize OD and find OD path

        file_name =  "vial{0}_ODset.txt".format(x)
        ODset_path = os.path.join(eVOLVER.exp_dir, 'ODset', file_name)
        data = np.genfromtxt(ODset_path, delimiter=',')
        ODset = data[len(data)-1][1]
        ODsettime = data[len(data)-1][0]
        num_curves=len(data)/2;

        file_name =  "vial{0}_OD.txt".format(x)
        OD_path = os.path.join(eVOLVER.exp_dir, 'OD', file_name)
        data = fu.get_last_n_lines('OD', x, OD_values_to_average, eVOLVER.exp_dir) # TODO: make this function not grab first line if it's not a number
        average_OD = 0

        # Determine whether turbidostat dilutions are needed
        #enough_ODdata = (len(data) > 7) #logical, checks to see if enough data points (couple minutes) for sliding window
        collecting_more_curves = (num_curves <= (stop_after_n_curves + 2)) #logical, checks to see if enough growth curves have happened

        if data.size != 0:
            # Take median to avoid outlier
            od_values_from_file = data[:,1]
            try:
                average_OD = float(np.median(od_values_from_file))
            except Exception as e:
                print(f'Vial {x}: od_values_from_file {od_values_from_file}\n\t{e}')
                continue

            #if recently exceeded upper threshold, note end of growth curve in ODset, allow dilutions to occur and growthrate to be measured
            if (average_OD > upper_thresh[x]) and (ODset != lower_thresh[x]):
                text_file = open(ODset_path, "a+")
                text_file.write("{0},{1}\n".format(elapsed_time,
                                                   lower_thresh[x]))
                text_file.close()
                ODset = lower_thresh[x]
                # calculate growth rate
                eVOLVER.calc_growth_rate(x, ODsettime, elapsed_time)

            #if have approx. reached lower threshold, note start of growth curve in ODset
            if (average_OD < (lower_thresh[x] + (upper_thresh[x] - lower_thresh[x]) / 3)) and (ODset != upper_thresh[x]):
                text_file = open(ODset_path, "a+")
                text_file.write("{0},{1}\n".format(elapsed_time, upper_thresh[x]))
                text_file.close()
                ODset = upper_thresh[x]

            #if need to dilute to lower threshold, then calculate amount of time to pump
            if average_OD > ODset and collecting_more_curves:

                time_in = - (np.log(lower_thresh[x]/average_OD)*VOLUME)/flow_rate[x]

                if time_in > 20:
                    time_in = 20

                time_in = round(time_in, 2)

                file_name =  "vial{0}_pump_log.txt".format(x)
                file_path = os.path.join(eVOLVER.exp_dir,
                                         'pump_log', file_name)
                data = np.genfromtxt(file_path, delimiter=',')
                last_pump = data[len(data)-1][0]
                if (((elapsed_time - last_pump)*60) >= pump_wait): # if sufficient time since last pump, send command to Arduino
                    if not np.isnan(time_in):
                        logger.info('turbidostat dilution for vial %d' % x)
                        # influx pump
                        MESSAGE[x] = str(time_in)
                        # efflux pump
                        MESSAGE[x + 16] = str(round(time_in + time_out, 2))

                        file_name =  "vial{0}_pump_log.txt".format(x)
                        file_path = os.path.join(eVOLVER.exp_dir, 'pump_log', file_name)

                        text_file = open(file_path, "a+")
                        text_file.write("{0},{1}\n".format(elapsed_time, time_in))
                        text_file.close()
                    else:
                        print(f'Vial {x}: time_in is NaN, cancelling turbidostat dilution')
                        logger.warning(f'Vial {x}: time_in is NaN, cancelling turbidostat dilution')
                    
        else:
            logger.debug('not enough OD measurements for vial %d' % x)

    ##### END OF Turbidostat Control Code #####
    
    ##### SELECTION LOGIC #####
    for vial in vials:
        # Create a SteppedController instance for the current vial
        controller = step_control.SteppedController(vial, eVOLVER.exp_dir, dilution_window, logger, elapsed_time, eVOLVER)
        
        # Perform control operations for this vial
        MESSAGE = controller.control(MESSAGE, time_out, VOLUME, lower_thresh, flow_rate, bolus_slow)
    
    # send fluidic command only if we are actually turning on any of the pumps
    if MESSAGE != ['--'] * 48:
        eVOLVER.fluid_command(MESSAGE)
        logger.info(f'Pump MESSAGE = {MESSAGE}')


if __name__ == '__main__':
    print('Please run eVOLVER.py instead')
    logger.info('Please run eVOLVER.py instead')
