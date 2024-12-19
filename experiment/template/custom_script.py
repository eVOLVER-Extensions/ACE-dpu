#!/usr/bin/env python3

import numpy as np
import logging
import os.path
import time
import step_utils as su
import light_control
import pandas as pd
import traceback

# logger setup
logger = logging.getLogger(__name__)

##### USER DEFINED GENERAL SETTINGS #####

# If using the GUI for data visualization, do not change EXP_NAME!
# only change if you wish to have multiple data folders within a single
# directory for a set of scripts
EXP_NAME = 'data'

# Port for the eVOLVER connection. You should not need to change this unless you have multiple applications on a single RPi.
EVOLVER_PORT = 8081

##### Identify pump calibration files, define initial values for temperature, stirring, volume, power settings

GROWTH_CURVE_TIME = 0 # hours; experiment time after which to start turbidostat

#TEMP_INITIAL = [38] * 16 #degrees C, makes 16-value list
#Alternatively enter 16-value list to set different values
TEMP_INITIAL = [38,38,38,38,38,38,38,38,38,38,38,38,38,38,38,38]

STIR_INITIAL = [10]*16 #try 8,10,12 etc; makes 16-value list
STIR_CYCLE_VIALS = []
STIR_ON_TIME = 0.2 # hours
STIR_OFF_TIME = 1.8 # hours

#Alternatively enter 16-value list to set different values
#STIR_INITIAL = [7,7,7,7,8,8,8,8,9,9,9,9,10,10,10,10]

VOLUME =  25 #mL, determined by vial cap straw length
OPERATION_MODE = 'turbidostat' #use to choose between 'turbidostat' and 'chemostat' functions
# if using a different mode, name your function as the OPERATION_MODE variable

### Light Settings ###
LIGHT_CAL_FILE = 'light_cal.txt'
EXCEL_CONFIG_FILE = "experiment_configurations.xlsx"

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

    ### Stepped Evolution Settings ###
    ## Selection Step Settings ##
    selection_steps = {} # all of the selection value steps to take in this part of the evolution, from low to high
    # Format: {turbidostat_vial1: [step1, step2, ...], turbidostat_vial2: [step1, step2, ...], ...}
    # Manual setting example: {0: [0,0.1,0.2,0.3,...], 1: [0,0.2,0.4,0.6,...], ...}
    # Can be set automatically using below settings

    # Used to calculate steps in selection variable
    generate_steps = True # True if you want to automatically calculate steps; False if you want to manually input each step
    log_steps = [False]*14 + [True]*2 # True if you want logarithmic steps; False if linear
    # For the below settings, specify individually by writing a list of values for each vial, ie [20,20,20,20, 10,10,10,10, 5,5,5,5, 1,1,1,1]
    # selection_stock_conc = [3000,3000,0,0, 3000,3000,3000,3000, 3000,3000,3000,100, 100,100,100,100]  # stock concentrations for each vial; should be low enough that minimum selection level is possible given min_bolus_s
    selection_stock_concs = [0]*2 + [12.5] * 14 # stock concentrations for each vial; should be low enough that minimum selection level is possible given min_bolus_s
    max_selections = [0,0,5,5,       0.5,0.5,5,5,        0.5,0.5,5,5,       5,5,5,5] # maximum value your selection can go to; for chemical selection = proportion of stock concentration (don't want to use all of stock)
    min_selections = [0,0,0.25,0.25, 0.5,0.5,0.25,0.25,  0.5,0.5,0.25,0.25, 0.5,0.5,0.25,0.25] # minimum value your selection can go to
    selection_step_nums = [20]*12 + [10]*2 + [20]*2# number of steps between min_selection and max_selection

    ## Experiment Settings ##
    curves_to_start = 5 # number of growth curves to wait before starting selection; allows us to calculate WT growth rate
    min_curves_per_step = 5 # number of growth curves to wait before increasing selection
    min_step_time = 7 # hours; minimum time to spend on a step before increasing or decreasing selection; a reasonable value is at least one full doubling
    growth_stalled_time = 6 # hours; if growth rate measurement is stalled for this many hours, selection pressure will be eased
    max_growthrate = 0.07 # growth rate which triggers increase in selection; for example, if growth rate is 0.1, consider making the max_growthrate 0.06
    min_growthrate = 0.04 # lowest growth rate allowable for selection; if growth rate is less than this, selection pressure will be eased
    selection_units = 'ug/mL' # the units used for the selection variable. Used only for print outs, doesn't affect script
    rescue_dilutions = True # True to decrease selection chemical concentration to rescue cells by diluting to the last step if selection is too harsh (up to 20 seconds of dilution)
                            # False to allow to equilibrate to lower selection concentration over multiple dilutions
    rescue_threshold = 0.5 # ratio of the lower_thresh OD below which we will not dilute to rescue; ie if lower_thresh = 2 and rescue_threshold = 0.5, we will not dilute if OD < 1
    max_rescues = 2 # Number of allowed rescue dilutions
    # TODO: Make column names informative in data files
    # TODO: Save experiment variables in data file
    # TODO: Notify the user of number of change of settings and result of settings like: max curves possible in a step,
    # TODO: change x in for loops to vial for consistency
    # TODO: turn selection into function
    # TODO: find/make a function that checks that a variable is a positive int and not anything else
    # length of time before decreasing selection if growth stalls, how a rescue dilution will work
    ### End of Stepped Evolution Settings ###

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
    dilution_window = 3 # window on either side of a dilution to calculate dilution effect on OD
    ## End of General Fluidics Settings ##
    
    ##### END OF ADVANCED SETTINGS #####
    
    ##### VARIABLE INITIALIZATION #####
    ## Check that min_selection is high enough given stock concentration and bolus_slow ##
    for i, vial in enumerate(turbidostat_vials):
        if vial in selection_steps: # if steps defined manually
            min_selection = selection_steps[vial][0]
            max_selection = selection_steps[vial][-1]
        else:
            min_selection = min_selections[i]
            max_selection = max_selections[i]
        if min_selection > max_selection:
            logger.warning(f"Vial {vial}: min_selection {min_selection} must be less than max_selection {max_selection}.")
            eVOLVER.stop_exp()
            print('Experiment stopped, goodbye!')
            logger.warning('experiment stopped, goodbye!')
            raise ValueError(f"Vial {vial}: min_selection {min_selection} must be less than max_selection {max_selection}.")
        
        # Calculate concentration with the smallest bolus we can add
        min_conc = ((selection_stock_concs[vial] * bolus_slow) + (0 * VOLUME)) / (bolus_slow + VOLUME) # Adding bolus_slow stock into plain media
        if min_conc > min_selection:
            # Solve for stock concentration that will be able to add bolus_slow and reach min_conc
            new_stock_conc = ((min_selections[i] * (bolus_slow + VOLUME)) - (min_conc * VOLUME)) / bolus_slow
            logger.warning(f"Vial {vial}: min_selection must be greater than {round(min_conc, 3)}. Decrease stock concentration to at least {int(new_stock_conc)}.")
            eVOLVER.stop_exp()
            print('Experiment stopped, goodbye!')
            logger.warning('experiment stopped, goodbye!')
            raise ValueError(f"Vial {vial}: min_selection must be greater than {round(min_conc, 3)}. Decrease stock concentration to at least {int(new_stock_conc)}.")

    ## Selection Step Automatic Generation ##
    # Compare current selection settings to previous and print if they have changed
    for i, vial in enumerate(turbidostat_vials):
        if (selection_step_nums[vial] == 0) and (selection_steps == {}): # No steps
            continue

        # Print and log if the config is updated
        elif generate_steps:
            current_config = [elapsed_time, int(log_steps[i]), selection_stock_concs[i], min_selections[i], max_selections[i], selection_step_nums[i]]
            config_change = su.compare_configs('step_gen', vial, current_config) # Check if config has changed and write to file if it has
        
            if config_change: # generate steps automatically
                if min_selections[i] - max_selections[i] == 0: # Only one step
                    selection_steps[vial] = [min_selections[i]]
                elif log_steps[i]:
                    if min_selections[i] <= 0: # check if min_selection is greater than 0
                        logger.warning(f"Vial {vial}: min_selection must be greater than 0 for logarithmic steps.")
                        eVOLVER.stop_exp()
                        print('Experiment stopped, goodbye!')
                        logger.warning('experiment stopped, goodbye!')
                        raise ValueError(f"Vial {vial}: min_selection must be greater than 0 for logarithmic steps.") # raise an error if min_selection is less than 0
                    selection_steps[vial] = np.round(np.logspace(np.log10(min_selections[i]), np.log10(max_selections[i]), num=selection_step_nums[i]), 3)
                else: # Linear step generation
                    selection_steps[vial] = np.round(np.linspace(min_selections[i], max_selections[i], num=selection_step_nums[i]), 3)
            
                # Write steps to step_config file and log
                print(f"\nVial {vial}: Generated {len(selection_steps[vial])} steps from {min_selections[i]} to {max_selections[i]} {selection_units}")
                logger.info(f"Vial {vial}: Generated {len(selection_steps[vial])} steps from {min_selections[i]} to {max_selections[i]} {selection_units}")
                file_name =  f"vial{vial}_step_config.txt"
                file_path = os.path.join(eVOLVER.exp_dir, EXP_NAME, 'step_config', file_name)
                with open(file_path, "a+") as text_file:
                    line = ','.join(str(step) for step in selection_steps[vial]) # Convert the list to a string with commas as separators
                    text_file.write(line+'\n') # Write the string to the file, including a newline character
            
            else: # Load selection steps from config
                selection_steps[vial] = su.get_last_n_lines('step_config', vial, 1)[0]
        
        else: # If we set steps manually
            current_steps = [elapsed_time] + selection_steps[vial]
            current_config = current_steps
            config_change = su.compare_configs('step', vial, current_config) # Compare and write steps to file if different
            print(f"\nVial {vial}:")
            print(f"\tStep config changed | New Steps:\n\t {selection_steps[vial]}\n")
            logger.info(f"Vial {vial}: step config changed | New Steps: {selection_steps[vial]}")

        # Update step_log if the config is updated
        current_conc = su.get_last_n_lines('step_log', vial, 1)[0][3] # Get just the concentration from the last step
        if config_change and current_conc != 0: # TODO: what if the current conc = 0 and config change? Need a better way of skipping this if experiment just started
            # Update log file with new steps
            file_name = f"vial{vial}_step_log.txt"
            file_path = os.path.join(eVOLVER.exp_dir, EXP_NAME, 'step_log', file_name)
            text_file = open(file_path, "a+")
            text_file.write(f"{elapsed_time},{elapsed_time},{round(selection_steps[vial][0], 3)},{current_conc},CONFIG CHANGE\n") # Format: [elapsed_time, step_time, current_step, current_conc]
            text_file.close()
            logger.info(f"Vial {vial}: step log updated to first step: {round(selection_steps[vial][0], 3)} {selection_units}")
    ## End of Selection Step Initialization ##

    ##### END OF VARIABLE INITIALIZATION #####

    ##### Turbidostat Control Code Below #####

    # fluidic message: initialized so that no change is sent
    MESSAGE = ['--'] * 48
    for x in turbidostat_vials: #main loop through each vial
        # Update turbidostat configuration files for each vial
        # initialize OD and find OD path

        file_name =  "vial{0}_ODset.txt".format(x)
        ODset_path = os.path.join(eVOLVER.exp_dir, EXP_NAME, 'ODset', file_name)
        data = np.genfromtxt(ODset_path, delimiter=',')
        ODset = data[len(data)-1][1]
        ODsettime = data[len(data)-1][0]
        num_curves=len(data)/2;

        file_name =  "vial{0}_OD.txt".format(x)
        OD_path = os.path.join(eVOLVER.exp_dir, EXP_NAME, 'OD', file_name)
        data = su.get_last_n_lines('OD', x, OD_values_to_average) # TODO: make this function not grab first line if it's not a number
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
                file_path = os.path.join(eVOLVER.exp_dir, EXP_NAME,
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
                        file_path = os.path.join(eVOLVER.exp_dir, EXP_NAME, 'pump_log', file_name)

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
    # TODO?: Change step_log to selection_log - more clear what it is
    # TODO?: Start logging event types (ie DILUTION, DECREASE, RESCUE) and reasons for that change (GROWTH_STALLED, EXCEDED_MAX_GROWTH)
    for vial in turbidostat_vials:
        # Get all growth rate data for this vial (read in as a Pandas dataframe)
        file_name =  f"vial{vial}_gr.txt"
        gr_path = os.path.join(eVOLVER.exp_dir, EXP_NAME, 'growthrate', file_name)
        gr_data = pd.read_csv(gr_path, delimiter=',', header=1, names=['time', 'gr'], dtype={'time': float, 'gr': float})
        OD_data = su.get_last_n_lines('OD', vial, dilution_window*2) # Get OD data from before and after dilution

        # Check for selection start
        if (len(gr_data) >= curves_to_start) and (len(OD_data) == dilution_window*2): # If the number of growth curves is more than the number we need to wait
            # Find the current selection step
            steps = np.array(selection_steps[vial])
            last_step_log = su.get_last_n_lines('step_log', vial, 1)[0] # Format: [elapsed_time, step_change_time, current_step, current_conc]
            last_time = float(last_step_log[0]) # time of the last step log; includes concentration adjustment calculations for dilutions
            last_step_change_time = float(last_step_log[1]) # experiment time that selection level was last changed
            last_step = float(last_step_log[2]) # last selection target level (chemical concentration)
            last_conc = float(last_step_log[3]) # last selection chemical concentration in the vial
            
            ## Initialize Variables ##
            step_time = elapsed_time - last_step_change_time # how long we have spent on the current step
            step_changed_time = last_step_change_time # Initialize to last time we changed selection levels
            closest_step_index = np.argmin(np.abs(steps - last_step)) # Find the index of the closest step to the current step
            current_conc = last_conc # Initialize the current concentration to the last concentration
            current_step = last_step # Initialize the next step to the current step
            selection_status_message = '' # The message about what changed on this selection step that will be later logged in the step_log

            # if closest_step_index == 0 and last_conc == 0 and last_step_change_time == 0:
            #     logger.info(f"Vial {vial}: STARTING SELECTION")
            #     print(f"Vial {vial}: STARTING SELECTION")

            ## SELECTION LEVEL LOGIC ## 
            # Decision: whether to go to next step, decrease to previous step, or stay at current step
            try:
                # Determine the number of growth curves that have happened on the current step
                num_curves_this_step = len(gr_data[gr_data['time'] > last_step_change_time])
                # TODO?: Move rescue dilution to fluidics section
                
                # Wait for min_curves_per_step growth curves on each step before deciding on a selection level
                # TODO: Make selection level logic more clear. Growth stalling is the only exception to requiring min_curves_per_step
                if (step_time >= min_step_time):
                    last_gr_time = gr_data['time'].values[-1] # time of the last growth rate measurement (ie dilution time)
                    last_gr = gr_data.tail(min_curves_per_step)['gr'].median() # median growth rate over the last curves

                    selection_change = '' # Which change type we are making
                    reason = '' # The reason for the change
                    if ((elapsed_time-last_gr_time) > growth_stalled_time): # Check for lack of growth
                        selection_change = "DECREASE"
                        reason += "growth stalled"
                    if (last_gr < min_growthrate) and (num_curves_this_step >= min_curves_per_step): # Check for too low of a growth rate
                        selection_change = "DECREASE"
                        reason += "-LOW GROWTH RATE-"
                    if (last_gr > max_growthrate) and (num_curves_this_step >= min_curves_per_step): # Check for too high of a growth rate
                        selection_change = "INCREASE"
                        reason = "-HIGH GROWTH RATE-"
                    if selection_change != '':
                        selection_status_message += f'{selection_change}: {reason} | '

                    # DECREASE to the previous selection level because selection level is too high
                    if selection_change == "DECREASE":
                        if (closest_step_index == 0): # We have already decreased to the first step
                            logger.warning(f"Vial {vial}: DECREASING SELECTION to 0 because {reason} on FIRST selection step {current_step} {selection_units} | Change step range or change growth rate requirements")
                            print(f"WARNING:: Vial {vial}: DECREASING SELECTION to 0 because {reason} on FIRST selection step {current_step} {selection_units} | Change step range or change growth rate requirements")
                            current_step = 0
                        elif closest_step_index - 1 == 0:
                            current_step = steps[closest_step_index - 1]
                            logger.warning(f"Vial {vial}: DECREASING SELECTION because {reason} to FIRST selection step {current_step} {selection_units} | Change step range or change growth rate requirements")
                            print(f"WARNING::Vial {vial}: DECREASING SELECTION because {reason} to FIRST selection step {current_step} {selection_units} | Change step range or change growth rate requirements")
                        else:
                            current_step = steps[closest_step_index - 1]
                            logger.info(f"Vial {vial}: DECREASING SELECTION because {reason} | from {last_step} to {current_step} {selection_units}")
                            print(f"Vial {vial}: DECREASING SELECTION because {reason} | from {last_step} to {current_step} {selection_units}")
                        step_changed_time = elapsed_time # Reset the step changed time

                        # RESCUE DILUTION LOGIC #
                        rescue_count = su.count_rescues(vial) # Determine number of previous rescue dilutions since last selection increase
                        if rescue_dilutions and (rescue_count >= max_rescues):
                            logger.warning(f'Vial {vial}: SKIPPING RESCUE DILUTION | number of rescue dilutions since last selection increase ({rescue_count}) >= max_rescues ({max_rescues})')

                        elif rescue_dilutions and (np.median(OD_data[:,1]) > (lower_thresh[vial]*rescue_threshold)): # Make a dilution to rescue cells to lower selection level; however don't make one if OD is too low or we have already done the max number of rescues
                            # Calculate the amount to dilute to reach the new selection level
                            if last_step == 0:
                                dilution_factor = rescue_threshold
                            else:
                                dilution_factor = current_step / last_conc
                            if dilution_factor < rescue_threshold:
                                logger.warning(f'Vial {vial}: RESCUE DILUTION | dilution_factor: {round(dilution_factor, 3)} < {rescue_threshold}: setting to the rescue_threshold ({rescue_threshold}) | last step {last_step} | current step {current_step} {selection_units}')
                                dilution_factor = rescue_threshold
                            
                            # Set pump time_in for dilution and log the pump event
                            time_in = - (np.log(dilution_factor)*VOLUME)/flow_rate[vial] # time to dilute to the new selection level
                            if np.isnan(time_in): # Check time_in for NaN
                                logger.error(f'Vial {vial}: SKIPPING RESCUE DILUTION | time_in is NaN')
                                print(f'Vial {vial}: SKIPPING RESCUE DILUTION | time_in is NaN')
                            elif time_in <= 0:
                                logger.error(f'Vial {vial}: SKIPPING RESCUE DILUTION | time_in is <= 0')
                                print(f'Vial {vial}: SKIPPING RESCUE DILUTION | time_in is <= 0')
                            else: # Make a rescue dilution
                                if time_in > 20: # Limit the time to dilute to 20
                                    time_in = 20
                                    dilution_factor = np.exp((time_in*flow_rate[vial])/(-VOLUME)) # Calculate the new dilution factor
                                    print(f'Vial {vial}: RESCUE DILUTION | Unable to dilute to {current_step} {selection_units} (> 20 seconds pumping) | Diluting by {round(dilution_factor, 3)} fold')
                                    logger.info(f'Vial {vial}: RESCUE DILUTION | Unable to dilute to {current_step} {selection_units} (> 20 seconds pumping) | Diluting by {round(dilution_factor, 3)} fold')
                                else:
                                    print(f'Vial {vial}: RESCUE DILUTION | dilution_factor: {round(dilution_factor, 3)}')
                                    logger.info(f'Vial {vial}: RESCUE DILUTION | dilution_factor: {round(dilution_factor, 3)}')

                                time_in = round(time_in, 2)
                                MESSAGE[vial] = str(time_in) # influx pump
                                MESSAGE[vial + 16] = str(round(time_in + time_out,2)) # efflux pump
                                file_name =  f"vial{vial}_pump_log.txt"
                                file_path = os.path.join(eVOLVER.exp_dir, EXP_NAME, 'pump_log', file_name)
                                text_file = open(file_path, "a+")
                                text_file.write("{0},{1}\n".format(elapsed_time, time_in))
                                text_file.close()
                                selection_status_message += f'RESCUE DILUTION | '
                                # TODO: calculate growth rate from last dilution to rescue dilution
                                            
                    # INCREASE to the next selection level because selection level is too low
                    elif selection_change == "INCREASE": # TODO?: perhaps include 0 as first step in all cases, then we will increase to first non-zero step 
                        if current_step < steps[0]: # If we had decreased selection target to below the first step in the selection
                            current_step = steps[0] # Raise selection to the first step
                        elif (len(steps) == 1): # If there is only one step
                            current_step = steps[0] # Raise selection to the first step
                        elif closest_step_index < (len(steps) - 1): # If there is a next step
                            current_step = steps[closest_step_index + 1]

                        if closest_step_index == len(steps) - 2: # Warn the user that they are on second to last step
                            logger.warning(f"Vial {vial}: Reached SECOND TO LAST selection step | {current_step} {selection_units} | Change step range")
                            print(f"WARNING: Vial {vial}: Reached SECOND TO LAST selection step | {current_step} {selection_units} | Change step range")
                        elif (closest_step_index == len(steps) - 1) and (len(steps) > 1): # If there is no next step
                            logger.warning(f"Vial {vial}: Reached MAXIMUM selection step | {current_step} {selection_units} | Change step range")
                            print(f"WARNING: Vial {vial}: Reached MAXIMUM selection step | {current_step} {selection_units} | Change step range")

                        if last_step != current_step:
                            logger.info(f"Vial {vial}: INCREASE | Growth rate = {round(last_gr,3)} | Increasing selection from {last_step} to {current_step} {selection_units}")
                            print(f"Vial {vial}: INCREASE | Growth rate = {round(last_gr,3)} | Increasing selection from {last_step} to {current_step} {selection_units}")
                            step_changed_time = elapsed_time # Reset the step changed time
                    
            except Exception as e:
                print(f"Vial {vial}: Error in Selection LOGIC Step: \n\t{e}\nTraceback:\n\t{traceback.format_exc()}")
                logger.error(f"Vial {vial}: Error in Selection LOGIC Step: \n\t{e}\nTraceback:\n\t{traceback.format_exc()}")
                continue

            ## SELECTION DILUTION HANDLING AND SELECTION CHEMICAL PUMPING ##
            try:
                # CHEMICAL CONCENTRATION FROM DILUTION #
                # Load the last pump event
                last_dilution = su.get_last_n_lines('pump_log', vial, 1)[0] # Format: [elapsed_time, time_in]
                last_dilution_time = last_dilution[0] # time of the last pump event

                # Calculate the dilution factor based off of proportion of OD change
                OD_times = OD_data[:, 0]
                if (last_dilution_time == OD_times[-(dilution_window+1)]) and (last_conc != 0): # Waiting until we have dilution_window length OD data before and after dilution 
                    # Calculate current concentration of selection chemical
                    OD_before = np.median(OD_data[:dilution_window, 1]) # Find OD before and after dilution
                    OD_after = np.median(OD_data[-dilution_window:, 1])
                    dilution_factor = OD_after / OD_before # Calculate dilution factor
                    current_conc = last_conc * dilution_factor
                    # TODO rewrite last dilution_window steps to this concentration
                    selection_status_message += f'DILUTION {round(dilution_factor, 3)}X | '

                # SELECTION CHEMICAL PUMPING #
                # Determine whether to add chemical to vial
                if current_step > 0: # avoid dividing by zero or negatives
                    conc_ratio = current_conc / current_step
                else:
                    conc_ratio = 1 # ie we are not adding chemical if the step is < 0
                
                # TODO: make below if statements more clear
                # Calculate amount of chemical to add to vial; only add if below target concentration and above lower OD threshold
                if (conc_ratio < 1) and (np.median(OD_data[:,1]) > lower_thresh[vial]) and (current_step != 0):
                    # Bolus derived from concentration equation:: C_final = [C_a * V_a + C_b * V_b] / [V_a + V_b]
                    calculated_bolus = (VOLUME * (current_conc - current_step)) / (current_step - selection_stock_concs[vial]) # in mL, bolus size of stock to add
                    if calculated_bolus > 5: # prevent more than 5 mL added at one time to avoid overflows
                        calculated_bolus = 5
                        # TODO?: add efflux event? How much will volume increase before next efflux otherwise?
                        # Update current concentration because we are not bringing to full target conc
                        current_conc = ((selection_stock_concs[vial] * calculated_bolus) + (current_conc * VOLUME)) / (calculated_bolus + VOLUME) 
                        print(f'Vial {vial}: Selection chemical bolus too large (adding 5mL) | current concentration {round(current_conc, 3)} {selection_units} | current step {current_step}')
                        logger.info(f'Vial {vial}: Selection chemical bolus too large (adding 5mL) | current concentration {round(current_conc, 3)} {selection_units} | current step {current_step}')
                    elif calculated_bolus < bolus_slow:
                        logger.info(f'Vial {vial}: Selection chemical bolus too small: current concentration {round(current_conc, 3)} {selection_units} | current step {current_step}')
                        # print(f'Vial {vial}: Selection chemical bolus too small: current concentration {round(current_conc, 3)} {selection_units} | current step {current_step}')
                        calculated_bolus = 0
                    else:
                        print(f'Vial {vial}: Selection chemical bolus added, {round(calculated_bolus, 3)}mL | {current_step} {selection_units}')
                        logger.info(f'Vial {vial}: Selection chemical bolus added, {round(calculated_bolus, 3)}mL | {current_step} {selection_units}')
                        current_conc = current_step

                    if calculated_bolus != 0 and not np.isnan(calculated_bolus):
                        time_in = calculated_bolus / float(flow_rate[vial + 32]) # time to add bolus
                        time_in = round(time_in, 2)
                        MESSAGE[vial + 32] = str(time_in) # set the pump message
                    
                        # Update slow pump log
                        file_name =  f"vial{vial}_slow_pump_log.txt"
                        file_path = os.path.join(eVOLVER.exp_dir, EXP_NAME, 'slow_pump_log', file_name)
                        text_file = open(file_path, "a+")
                        text_file.write("{0},{1}\n".format(elapsed_time, time_in))
                        text_file.close()
                        selection_status_message += f'SELECTION CHEMICAL ADDED {round(calculated_bolus, 3)}mL | '

                elif (np.median(OD_data[:,1]) < lower_thresh[vial]) and (current_step != 0):
                    logger.info(f'Vial {vial}: SKIPPED selection chemical bolus: OD {round(np.median(OD_data[:,1]), 2)} below lower OD threshold {lower_thresh[vial]}')
                    selection_status_message += f'SKIPPED SELECTION CHEMICAL - LOW OD {round(np.median(OD_data[:,1]), 2)} | '

                # Log current selection state
                if (step_changed_time != last_step_change_time) or (current_step != last_step) or (current_conc != last_conc) or (selection_status_message != ''): # Only log if step changed or conc changed
                    file_name =  f"vial{vial}_step_log.txt"
                    file_path = os.path.join(eVOLVER.exp_dir, EXP_NAME, 'step_log', file_name)
                    text_file = open(file_path, "a+")
                    text_file.write(f"{elapsed_time},{step_changed_time},{current_step},{round(current_conc, 5)},{selection_status_message}\n") # Format: [elapsed_time, step_changed_time, current_step, current_conc]
                    text_file.close()

            except Exception as e:
                print(f"Vial {vial}: Error in Selection Fluidics Step: \n\t{e}\nTraceback:\n\t{traceback.format_exc()}")
                logger.error(f"Vial {vial}: Error in Selection Fluidics Step: \n\t{e}\nTraceback:\n\t{traceback.format_exc()}")
                continue
    
    # send fluidic command only if we are actually turning on any of the pumps
    if MESSAGE != ['--'] * 48:
        eVOLVER.fluid_command(MESSAGE)
        logger.info(f'Pump MESSAGE = {MESSAGE}')

    #### LIGHT CONTROL CODE BELOW ####
    light_control.control(eVOLVER, vials, elapsed_time, logger, EXP_NAME)

    #### STIR CONTROL CODE BELOW ####
    stir_MESSAGE = ['--'] * 16
    for vial in turbidostat_vials:
        if vial in STIR_CYCLE_VIALS:
            # Calculate current cycle position based on the elapsed time since cycle_start
            cycle_duration = STIR_ON_TIME + STIR_OFF_TIME    # Total duration of one stir cycle (ON + OFF)
            time_in_cycle = elapsed_time % cycle_duration  # Position in the current cycle

            # Determine if the current time is in the ON or OFF phase
            if time_in_cycle < STIR_OFF_TIME:
                # Stir is OFF
                stir_MESSAGE[vial] = 0
                if time_in_cycle < 0.1:
                    logger.info(f'Vial {vial}: Stir OFF')
            else:
                # Stir is ON
                stir_MESSAGE[vial] = STIR_INITIAL[vial]
                logger.info(f'Vial {vial}: Stir ON')
        else:
            # Stir is ON
            stir_MESSAGE[vial] = STIR_INITIAL[vial]
    eVOLVER.update_stir_rate(stir_MESSAGE, immediate = True) # Update the stir rate

if __name__ == '__main__':
    print('Please run eVOLVER.py instead')
    logger.info('Please run eVOLVER.py instead')
