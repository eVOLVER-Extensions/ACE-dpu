# These are functions used in the custom_script.py file to control a stepped evolution experiment
# from custom_script import EXP_NAME
EXP_NAME = 'data'
import numpy as np
import logging
import os.path
import pandas as pd
from scipy.stats import linregress

#### GENERAL HELPER FUNCTIONS ####
def tail_to_np(path, window=10, BUFFER_SIZE=512):
    """
    Reads file from the end and returns a numpy array with the data of the last 'window' lines.
    Alternative to np.genfromtxt(path) by loading only the needed lines instead of the whole file.
    """
    try:
        f = open(path, 'rb')
    except OSError as e:
        print(f"Unable to open file: {path}\n\tError: {e}")
        return np.asarray([])

    if window == 0:
        return np.asarray([])

    f.seek(0, os.SEEK_END)
    remaining_bytes = f.tell()
    size = window + 1  # Read one more line to avoid broken lines
    block = -1
    data = []

    while size > 0 and remaining_bytes > 0:
        if remaining_bytes - BUFFER_SIZE > 0:
            # Seek back one whole BUFFER_SIZE
            f.seek(block * BUFFER_SIZE, os.SEEK_END)
            # read BUFFER
            bunch = f.read(BUFFER_SIZE)
        else:
            # file too small, start from beginning
            f.seek(0, 0)
            # only read what was not read
            bunch = f.read(remaining_bytes)

        bunch = bunch.decode('utf-8')
        data.append(bunch)
        size -= bunch.count('\n')
        remaining_bytes -= BUFFER_SIZE
        block -= 1

    f.close()
    data = ''.join(reversed(data)).splitlines()[-window:]

    if len(data) < window:
        # Not enough data
        return np.asarray([])

    for c, v in enumerate(data):
        data[c] = v.split(',')

    try:
        data = np.asarray(data, dtype=np.float64)
        return data
    except:
        try:
            return np.asarray(data)
        except e:
            print(f"tail_to_np: Unable to read file as numpy array: {path}\n\tError: {e}")
            return np.asarray([])

def get_last_n_lines(var_name, vial, n_lines):
    """
    Retrieves the last lines of the file for a given variable name and vial number.
    Args:
        var_name (str): The name of the variable.
        vial (int): The vial number.
        n_lines (int): The number of lines to retrieve.
    Returns:
        numpy.ndarray: Returns the last n lines of the file.
    """
    # Construct file name and path
    file_name = f"vial{vial}_{var_name}.txt"
    if var_name == "gr":
        var_name = "growthrate"
    file_path = os.path.join(EXP_NAME, f'{var_name}', file_name)

    try:
        data = tail_to_np(file_path, n_lines)
        if data.ndim == 0:
            return data[0]
        return data
    except Exception as e:
        print(f"Unable to read file using tail_to_np: {file_path}.\n\tError: {e}")
        try:
            data = np.genfromtxt(file_path, delimiter=',', skip_header=0)  # Adjust delimiter as necessary
            return data[-n_lines:]
        except Exception as e:
            print(f"Unable to read file using np.genfromtxt: {file_path}.\n\tError: {e}")
            return np.asarray([])
        
def labeled_last_n_lines(var_name, vial, n_lines):
    """
    Gets the last n lines of a variable in a vial's data, then labels them with the header from the CSV file.
    Args:
        var_name (str): The name of the variable.
        vial (int): The vial number.
        n_lines (int): The number of lines to retrieve.
    Returns:
        pd.DataFrame: The last n lines of the variable, with headers.
    """
    file_name = f"vial{vial}_{var_name}.txt"
    path = os.path.join(EXP_NAME, var_name, file_name)

    with open(path, 'r') as file:
        heading = file.readline().strip().split(',')
    
    data = get_last_n_lines(var_name, vial, n_lines)
    if data.ndim == 0:
        return pd.DataFrame(data, columns=[heading])
    return pd.DataFrame(data, columns=heading)

def compare_configs(var_name, vial, current_config):
    """
    Compare the current configuration with the last configuration for a given variable and vial. Ignores the time in index 0.
    Args:
        var_name (str): The name of the variable.
        vial (int): The name of the vial.
        current_config (list): The current configuration.
    Returns:
        bool: True if the current configuration is different from the last configuration, False otherwise.
    """
    current_config = np.asarray(current_config, dtype=np.float64)  
    # Open the config file
    file_name = f"vial{vial}_{var_name}_config.txt"
    if var_name == "gr":
        var_name = "growthrate"
    config_path = os.path.join( EXP_NAME, f'{var_name}_config', file_name)
    with open(config_path, 'r') as file:
        # Read all lines from the file
        lines = file.readlines()
        # Get the last line
        last_config = (lines[-1])
        last_config = np.fromstring(last_config, dtype=np.float64, sep=',')

    # Check if config has changed
    if not np.array_equal(last_config[1:], current_config[1:]): # ignore the times in index 0, see if arrays are the same
        # Write the updated configuration to the config file
        with open(config_path, "a+") as text_file:
            line = ','.join(str(config) for config in current_config) # Convert the list to a string with commas as separators
            text_file.write(line+'\n') # Write the string to the file, including a newline character
        return True # If the arrays are not the same, return True
    else:
        return False # If the arrays are the same, return False


#### MATH FUNCTIONS ####
def exponential_growth(x, a, b):
    """
    Exponential growth model function.
    
    Args:
    x (array-like): The independent variable where the data is measured (time or equivalent).
    a (float): Initial amount.
    b (float): Growth rate coefficient.
    
    Returns:
    array-like: Computed exponential growth values.
    """
    return a * np.exp(b * x)

def count_rescues(vial):
    """
    Counts the occurrences of 'RESCUE' since the last 'INCREASE' message 
    from the specified log file.

    Parameters:
    vial (int): The vial number to identify the specific log file.

    Returns:
    int: The number of 'RESCUE' occurrences since the last 'INCREASE' message.
    """
    file_name = f"vial{vial}_step_log.txt"
    file_path = os.path.join(EXP_NAME, 'step_log', file_name)

    try:
        with open(file_path, "r") as text_file:
            lines = text_file.readlines()
    except FileNotFoundError:
        print(f"Error: The log file {file_path} was not found.")
        return 0
    except Exception as e:
        print(f"Error reading file {file_path}: {e}")
        return 0

    # Reverse the messages to start counting from the latest one
    reversed_messages = lines[::-1]

    # Initialize the rescue count
    rescue_count = 0

    # Iterate over the messages
    for msg in reversed_messages:
        if 'INCREASE' in msg:
            break
        elif 'RESCUE' in msg:
            rescue_count += 1

    return rescue_count
