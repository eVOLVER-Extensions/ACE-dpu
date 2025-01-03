import os
import numpy as np
import matplotlib.pyplot as plt
from . import file_utils as fu
from . import config_utils as cu

def update_selection_configs(elapsed_time, vials, configs, logger, eVOLVER):
    """
    Updates selection configs for the given vials and automatically generates new steps if necessary.
    
    Parameters:
        elapsed_time (float): The elapsed time since the start of the experiment.
        vials (list): A list of vials to update the configs for.
        configs (dict): A dictionary containing configuration data for 'selection-control' and 'selection-step_generation'.
    Returns:
        None
    """
    exp_dir = eVOLVER.exp_dir
    cu.update_config_files(vials, configs['selection-control'], 'selection-control', elapsed_time, logger, exp_dir) # Update the selection control configs
    cu.update_config_files(vials, configs['selection-step_generation'], 'selection-step_generation', elapsed_time, logger, exp_dir) # Update the selection step generation configs

    update_steps(vials, configs, elapsed_time, logger, eVOLVER)

def update_steps(vials, configs, elapsed_time, logger, eVOLVER):
    """
    Update the selection steps for each vial based on the provided configurations.
    Parameters:
        vials (list): List of vial identifiers.
        configs (dict): Dictionary containing configuration dataframes for 'selection-steps', 
                    'selection-step_generation', and 'selection-control'.
        elapsed_time (float): The elapsed time since the start of the experiment.
        logger (logging.Logger): Logger instance for logging information and debug messages.
    Returns:
        None
    """
    selection_steps = {}

    for vial in vials:
        control_config = configs['selection-control'].loc[vial]
        step_type = control_config.step_type.lower() # Get the step type for this vial and convert to lower case
        selection_units = control_config.selection_units # Get the units for the selection steps
        manual_steps = configs['selection-steps'].loc[vial].steps # Get the manual steps for this vial, if any
        step_gen_config = configs['selection-step_generation'].loc[vial]

        # Manually entered steps
        if step_type == 'manual':
            # print(f'MANUAL selection steps for vial {vial}')
            selection_steps[vial] = parse_manual_steps(vial, manual_steps)

        # No selection on this vial
        elif (step_type == 'off') or (control_config.stock_concentration == 0):
            selection_steps[vial] = [0]
            # print(f'NO SELECTION for vial {vial}')
            # logger.info(f'No selection steps for vial {vial}')

        # Generated steps
        elif step_type == 'auto':
            # Generate steps for the given vial
            # print(f'GENERATING selection steps for vial {vial}')
            # print(step_gen_config)
            selection_steps[vial] = generate_selection_steps(step_gen_config, logger, eVOLVER)
        
        # Invalid step type
        else:
            logger.warning(f"Vial {vial}: Invalid step type '{step_type}' in selection-control configuration.")
            eVOLVER.stop_exp()
            print('Experiment stopped, goodbye!')
            logger.warning('experiment stopped, goodbye!')
            raise ValueError(f"Vial {vial}: Invalid step type '{step_type}' in selection-control configuration.\n\tValid step types are 'manual', 'generated', and 'off'.")
        
    # Update the selection step configuration
    update_step_configs(vials, selection_steps, 'selection-steps', selection_units, elapsed_time, logger, eVOLVER)

def update_step_configs(vials, selection_steps, config_name, selection_units, elapsed_time, logger, eVOLVER):
    """
    Updates the configuration files for the given vials based on the selection steps and elapsed time. Creates files if they do not exist.
    Parameters:
        vials (list): List of vial identifiers to update.
        selection_steps (dict): Dictionary mapping vial identifiers to their respective selection steps.
        config_name (str): Name of the configuration to update.
        selection_units (str): Units of the selection steps.
        elapsed_time (float): The elapsed time since the start of the experiment.
        logger (logging.Logger): Logger object for logging information.
    Returns:
        list: List of vials that have had their configurations updated.
    """
    config_directory = os.path.join(eVOLVER.exp_dir, f'{config_name}')
    updated_vials = [] # List of vials that have had their configs updated

    # Create config files and directory if they do not exist
    if not os.path.exists(config_directory):
        os.makedirs(config_directory)
        for vial in vials:
            current_config = [elapsed_time] + selection_steps[vial]
            with open(os.path.join(config_directory, f'vial{vial}_{config_name}.txt'), 'w') as file:
                file.write(','.join(map(str, current_config)) + '\n')
            updated_vials.append(vial)
            print(f'Vial {vial}: updating {config_name} config')
            logger.info(f'Vial {vial}: updating {config_name} config')

    # Compare the current config to the last line of the old config
    else:
        for vial in vials:
            current_config = [elapsed_time] + selection_steps[vial]
            config_changed = cu.compare_configs(vial, current_config, config_name, eVOLVER.exp_dir, ignore_time=True) # compare the current config to the last line of the old config

            if config_changed: # Update and log config change
                cu.update_config(vial, config_name, current_config, eVOLVER.exp_dir) # Update the config file
                print(f'Vial {vial}: updating {config_name} config')
                logger.info(f'Vial {vial}: updating {config_name} config')
                updated_vials.append(vial)
    
                # Update step_log if the config is updated
                current_conc = fu.get_last_n_lines('step_log', vial, 1, eVOLVER.exp_dir)[0][3] # Get just the concentration from the last step
                if current_conc != 0: # TODO: what if the current conc = 0 and config change? Need a better way of skipping this if experiment just started
                    # Update log file with new steps
                    file_name = f"vial{vial}_step_log.txt"
                    file_path = os.path.join(eVOLVER.exp_dir, 'step_log', file_name)
                    text_file = open(file_path, "a+")
                    text_file.write(f"{elapsed_time},{elapsed_time},{round(selection_steps[vial][0], 3)},{current_conc},CONFIG CHANGE\n") # Format: [elapsed_time, step_time, current_step, current_conc]
                    text_file.close()
                    logger.info(f"Vial {vial}: step log updated to first step: {round(selection_steps[vial][0], 3)} {selection_units}")  
    
    return updated_vials

def parse_manual_steps(vial, steps):
    """
    Parses the manual steps input and returns a list of steps.
    Args:
        vial (str): The identifier for the vial.
        steps (int or str): The steps to be parsed. Can be an integer or a comma-separated string of numbers.
    Returns:
        list: A list of steps as floats.
    Raises:
        ValueError: If the steps string cannot be parsed into floats.
    """
    if type(steps) == int: # If the steps are an integer, return a list with that integer
        return [steps]
    
    try:
        return list(map(float, steps.strip().split(','))) # Convert the string of commas to a list of floats
    except Exception as e:
        raise ValueError(f"Error parsing manual steps for vial {vial}:\n\t{e}")
    
def generate_selection_steps(step_gen_config, logger, eVOLVER):
    """
    Generates or validates selection steps for a single vial and logs configuration changes.

    Parameters:
        step_gen_config: Dictionary containing step generation configuration.
            - vial: List of vial indices.
            - log_steps: List indicating if logarithmic steps should be used per vial.
            - stock_concentrations: List of stock concentrations per vial.
            - min_selection: List of minimum selection values per vial.
            - max_selection: List of maximum selection values per vial.
            - selection_step_nums: Number of steps between min and max selection per vial.
        elapsed_time: Current elapsed time of the experiment.
        logger: Logger for warnings and updates.
        eVOLVER: eVOLVER instance for stopping experiments.
    Returns:
        list: List of selection steps for the vial.
    """
    # Unpack step generation configuration
    vial = step_gen_config['vial']
    log_steps = step_gen_config['logarithmic_steps']
    # stock_concentration = step_gen_config['stock_concentration']
    min_selection = step_gen_config['min_selection']
    max_selection = step_gen_config['max_selection']
    step_number = step_gen_config['step_number']

    if min_selection - max_selection == 0: # Only one step
        selection_steps = [min_selection]
    elif log_steps: # Logarithmic step generation
        if min_selection <= 0: # check if min_selection is greater than 0
            logger.warning(f"Vial {vial}: min_selection must be greater than 0 for logarithmic steps.")
            eVOLVER.stop_exp()
            print('Experiment stopped, goodbye!')
            logger.warning('experiment stopped, goodbye!')
            raise ValueError(f"Vial {vial}: min_selection must be greater than 0 for logarithmic steps.") # raise an error if min_selection is less than 0
        selection_steps = np.round(np.logspace(np.log10(min_selection), np.log10(max_selection), num=step_number), 3)
    else: # Linear step generation
        selection_steps = np.round(np.linspace(min_selection, max_selection, num=step_number), 3)
    
    return list(selection_steps)

# Plot steps for all vials in a 4x4 grid as a step plot
def plot_steps(vials, config_name, step_type, exp_dir):
    """
    Plots the steps for all vials in a 4x4 grid as a step plot.
    Args:
        vials (list): A list of vials.
        config_name (str): The name of the configuration.
        step_type (str): The type of step.
        save_path (str): The path to save the plot.
    Returns:
        None
    """
    fig, axs = plt.subplots(4, 4, figsize=(16, 16))
    fig.suptitle(f'{step_type} steps for all vials', fontsize=16)
    for i, vial in enumerate(vials):
        data = fu.get_last_n_lines(config_name, vial, 1, exp_dir)[0]
        axs[i//4, i%4].step(range(1, len(data)), data[1:], where='post')
        axs[i//4, i%4].set_title(f'Vial {vial}')
        axs[i//4, i%4].set_xlabel('Step Number')
        axs[i//4, i%4].set_ylabel(f'{step_type}')
    plt.tight_layout(rect=[0, 0, 1, 0.95])
    plt.savefig(os.path.join(exp_dir, f'{step_type}_steps.png'))
    plt.show()

# def validate_selection_parameters(vials, selection_steps, min_selections, max_selections, 
#                                   stock_concentrations, bolus_slow, volume, logger, eVOLVER):
#     """
#     Validates the selection parameters for each vial and ensures constraints are satisfied.

#     Parameters:
#     - vials: List of vial indices.
#     - selection_steps: Dictionary defining selection steps for each vial.
#     - min_selections: List of minimum selection values per vial.
#     - max_selections: List of maximum selection values per vial.
#     - stock_concentrations: List of stock concentrations per vial.
#     - bolus_slow: Smallest bolus volume that can be added (mL).
#     - volume: Total volume in the vial (mL).
#     - logger: Logger for warnings and errors.
#     - eVOLVER: eVOLVER instance for stopping experiments.

#     Raises:
#     - ValueError: If any vial has invalid selection parameters.
#     """
#     for i, vial in enumerate(vials):
#         # Check if steps are defined manually or will be generated if both, raise an error
        
#         if vial in selection_steps: # if steps defined manually
#             min_selection = selection_steps[vial][0]
#             max_selection = selection_steps[vial][-1]
#         else:
#             min_selection = min_selections[i]
#             max_selection = max_selections[i]
#         if min_selection > max_selection:
#             logger.warning(f"Vial {vial}: min_selection {min_selection} must be less than max_selection {max_selection}.")
#             eVOLVER.stop_exp()
#             print('Experiment stopped, goodbye!')
#             logger.warning('experiment stopped, goodbye!')
#             raise ValueError(f"Vial {vial}: min_selection {min_selection} must be less than max_selection {max_selection}.")
        
#         # Calculate concentration with the smallest bolus we can add
#         min_conc = ((stock_concentrations[vial] * bolus_slow) + (0 * volume)) / (bolus_slow + volume) # Adding bolus_slow stock into plain media
#         if min_conc > min_selection:
#             # Solve for stock concentration that will be able to add bolus_slow and reach min_conc
#             new_stock_conc = ((min_selections[i] * (bolus_slow + volume)) - (min_conc * volume)) / bolus_slow
#             logger.warning(f"Vial {vial}: min_selection must be greater than {round(min_conc, 3)}. Decrease stock concentration to at least {int(new_stock_conc)}.")
#             eVOLVER.stop_exp()
#             print('Experiment stopped, goodbye!')
#             logger.warning('experiment stopped, goodbye!')
#             raise ValueError(f"Vial {vial}: min_selection must be greater than {round(min_conc, 3)}. Decrease stock concentration to at least {int(new_stock_conc)}.")



if __name__ == "__main__":
    print("This module provides initialization functions for step-based selection experiments.")
    print('Please run eVOLVER.py instead')
