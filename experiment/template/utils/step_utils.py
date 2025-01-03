import numpy as np
import os.path

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

#### FUNCTIONS THAT AID SELECTION CONTROL ####
def count_rescues(vial, eVOLVER):
    """
    Counts the occurrences of 'RESCUE' since the last 'INCREASE' message 
    from the specified log file.

    Parameters:
    vial (int): The vial number to identify the specific log file.

    Returns:
    int: The number of 'RESCUE' occurrences since the last 'INCREASE' message.
    """
    file_name = f"vial{vial}_step_log.txt"
    file_path = os.path.join(eVOLVER.exp_dir, 'step_log', file_name)

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

if __name__ == '__main__':
    print('Please run eVOLVER.py instead')
    