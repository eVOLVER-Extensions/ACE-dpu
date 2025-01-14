import os
import pandas as pd

#### UTILITIES FOR WORKING WITH CONFIG FILES ####
def load_excel_configs(config_filename):
    """
    Load configurations from an Excel file.
    This function reads an Excel file containing multiple sheets, where each sheet
    represents a different configuration. It returns a dictionary where the keys
    are the sheet names and the values are pandas DataFrames containing the data
    from each sheet.
    Args:
        config_filename (str): The path to the Excel file containing the configurations.
    Returns:
        dict: A dictionary with sheet names as keys and pandas DataFrames as values.
    """
    
    # Load the Excel file
    excel_file = pd.ExcelFile(config_filename)

    # Get the list of configs
    config_names = excel_file.sheet_names

    # Iterate through each sheet and save each config as a pandas DataFrame
    configs = {}
    for config_name in config_names:
        # Read the sheet into a DataFrame
        config = pd.read_excel(excel_file, sheet_name=config_name)
        configs[config_name] = config
        
    # Explicitly close the file handle
    excel_file.close()

    return configs

def update_config(vial, config_name, current_config, exp_dir):
    """
    Update a configuration file with a new configuration.
    Args:
        vial (int): The vial number to identify the configuration file.
        config_name (str): The name of the configuration.
        current_config (list of str): The new configuration as a list of strings. The first position is the elapsed time.
        exp_dir (str): The directory where the configuration files are stored.
    Returns:
        None
    """

    config_path = os.path.join(exp_dir, f'{config_name}', f'vial{vial}_{config_name}.txt')
    
    with open(config_path, "a+") as text_file:
        line = ','.join(str(config) for config in current_config) # Convert the list to a string with commas as separators
        text_file.write(line+'\n') # Write the string to the file, including a newline character

def compare_configs(vial, current_config, config_name, exp_dir, ignore_time=True):
    """
    Compare the current configuration with the last configuration for a given variable and vial. Ignores the time in index 0.
    Args:
        vial (int): The name of the vial.
        current_config (list of strings): The current configuration as a list of strings.
        config_name (str): The name of the variable.
        exp_dir (str): The directory where the configuration files are stored.
        ignore_time (bool): Whether to ignore the time in index 0 when comparing configurations.
    Returns:
        bool: True if the current configuration is different from the last configuration, False otherwise.
    """
    # Turn the current_config into a list of strings
    current_config = [str(config) for config in current_config]

    # Open the config file
    file_name = f"vial{vial}_{config_name}.txt"
    if config_name == "gr":
        config_name = "growthrate"
    config_path = os.path.join(exp_dir, f'{config_name}', file_name)
    with open(config_path, 'r') as file:
        # Read all lines from the file
        lines = file.readlines()
        # Get the last line
        last_config = lines[-1].strip().split(',')
    
    # Check if config has changed
    if len(current_config) != len(last_config):
        return True
    for i in range(ignore_time, len(current_config)): # ignore the times in index 0
        if current_config[i] != last_config[i]:
            return True
    return False # If the arrays are the same, return False

def update_config_files(vials, config, config_name, elapsed_time, logger, exp_dir):
    """
    Update configuration files for one configuration type for the given vials. If the configuration files do not exist, they are created. 
    
    This function updates configuration files for a set of vials based on the provided configuration.
    If the configuration files do not exist, they are created. If they do exist, the function compares
    the current configuration with the last line of the existing configuration file and updates it if
    there are changes.
    
    Parameters:
    elapsed_time (str): The elapsed time to be added to the configuration.
    vials (list): A list of vial identifiers.
    config (DataFrame): A pandas DataFrame containing the configuration data.
    config_name (str): The name of the configuration.
    logger (Logger): The logger object.
    exp_dir (str): The directory where the configuration files are stored.
    Returns:
    None
    """
    
    config_directory = os.path.join(exp_dir, f'{config_name}')
    config_copy = config.copy()
    
    updated_vials = [] # List of vials that have had their configs updated

    # Create config files if they do not exist
    if not os.path.exists(config_directory):
        os.makedirs(config_directory)
        for vial in vials:
            vial_config = config_copy[config_copy['vial'] == vial] # get as DataFrame
            vial_config = vial_config.rename(columns={'vial': 'elapsed_time'}) # remove the vial column
            vial_config['elapsed_time'] = elapsed_time # add the elapsed time
            vial_config.to_csv(os.path.join(config_directory, f'vial{vial}_{config_name}.txt'), header=True, index=False)
            updated_vials.append(vial)
            print(f'Vial {vial}: updating {config_name} config')
            logger.info(f'Vial {vial}: updating {config_name} config')

    # Compare the current config to the last line of the old config
    else:
        for vial in vials:
            current_config = config_copy.loc[vial].values.astype(str).tolist()
            current_config[0] = elapsed_time # replace first value in current config with elapsed time
            config_changed = compare_configs(vial, current_config, config_name, exp_dir, ignore_time=True) # compare the current config to the last line of the old config

            if config_changed: # Update and log config change
                update_config(vial, config_name, current_config, exp_dir)
                print(f'Vial {vial}: updating {config_name} config')
                logger.info(f'Vial {vial}: updating {config_name} config')
                updated_vials.append(vial)
    return updated_vials

if __name__ == '__main__':
    print('Please run eVOLVER.py instead')
