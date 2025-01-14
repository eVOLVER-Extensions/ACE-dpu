import os
import traceback
import numpy as np
import pandas as pd

import utils.step_utils as su
import utils.file_utils as fu
import utils.config_utils as cu

class SteppedController:
    def __init__(self, vial, exp_dir, dilution_window, logger, elapsed_time, eVOLVER):
        self.vial = vial
        self.exp_dir = exp_dir
        self.dilution_window = dilution_window
        self.logger = logger
        self.elapsed_time = elapsed_time
        self.eVOLVER = eVOLVER
        self.gr_data, self.OD_data, self.selection_steps, self.selection_controls, self.last_step_log = self.load_info()
        self.selection_status_message = ''
        
        # Additional Parameters
        self.max_selection_bolus = 5 # mL; Maximum amount of selection chemical to add at one time to prevent overflows
        self.fold_decrease = 0.5 # Fold to decrease when going below lowest step

        # Selection Controls
        self.selection_type = self.selection_controls.selection_type.lower()
        self.stock_conc = float(self.selection_controls.stock_concentration)
        self.curves_to_start = int(self.selection_controls.curves_to_start)
        self.min_curves_per_step = int(self.selection_controls.min_curves_per_step)
        self.min_step_time = float(self.selection_controls.min_step_time)
        self.growth_stalled_time = float(self.selection_controls.growth_stalled_time)
        self.min_growthrate = float(self.selection_controls.min_growthrate)
        self.max_growthrate = float(self.selection_controls.max_growthrate)
        self.rescue_dilutions = int(self.selection_controls.rescue_dilutions)
        self.rescue_threshold = float(self.selection_controls.rescue_threshold)
        self.selection_units = self.selection_controls.selection_units

        # Step Log
        self.last_time = float(self.last_step_log[0])
        self.last_step_change_time = float(self.last_step_log[1])
        self.last_step = float(self.last_step_log[2])
        self.last_conc = float(self.last_step_log[3])

        self.step_time = self.elapsed_time - self.last_step_change_time
        self.step_changed_time = self.last_step_change_time
        self.closest_step_index = np.argmin(np.abs(self.selection_steps - self.last_step))
        self.current_conc = self.last_conc
        self.current_step = self.last_step
        
    def load_info(self):
        """Load growth rate, OD, and selection data for the given vial."""
        file_name = f"vial{self.vial}_gr.txt"
        gr_path = os.path.join(self.exp_dir, 'growthrate', file_name)
        gr_data = pd.read_csv(gr_path, delimiter=',', header=1, names=['time', 'gr'], dtype={'time': float, 'gr': float})

        OD_data = fu.get_last_n_lines('OD', self.vial, self.dilution_window * 2, self.exp_dir)
        selection_steps =fu.get_last_n_lines('selection-steps', self.vial, 1, self.exp_dir)[0][1:]
        selection_controls = fu.labeled_last_n_lines('selection-control', self.vial, 1, self.exp_dir).iloc[0]
        last_step_log = fu.get_last_n_lines('step_log', self.vial, 1, self.exp_dir)[0]

        return gr_data, OD_data, selection_steps, selection_controls, last_step_log

    def control(self, MESSAGE, time_out, VOLUME, lower_thresh, flow_rate, bolus_slow):
        """
        Main function to control stepped selection logic for a single vial in the eVOLVER system.

        Parameters:
        - MESSAGE: Fluidics command message for all vials.
        - time_out: Extra time to pump for efflux pumps
        - VOLUME: Volume of the vial in mL.
        - lower_thresh: Lower OD threshold for this vial.
        - flow_rate: Flow rate of ALL pumps in mL/s.
        - bolus_slow: Minimum bolus size for selection chemical addition.

        Returns:
        - MESSAGE: Fluidics command message for all vials, updated for this vial.
        """

        if not self.check_started():
            return MESSAGE
        
        self.determine_step()

        if self.selection_type == 'chemical':
            MESSAGE = self.adjust_concentration(MESSAGE, time_out, VOLUME, lower_thresh, flow_rate, bolus_slow)

        if self.selection_status_message: # Log the selection status message if there is one
            log_message = f"{self.step_changed_time},{self.current_step},{round(self.current_conc, 5)},{self.selection_status_message}"
            fu.update_log(self.vial, 'step_log', self.elapsed_time, log_message, self.exp_dir)

        return MESSAGE

    def check_started(self):
        """Check if the experiment has started based on the data available."""
        if self.OD_data.size < self.dilution_window * 2:
            return False

        if len(self.gr_data) < int(self.curves_to_start):
            return False

        if int(self.selection_steps[0]) == 0 and len(self.selection_steps) == 1:
            return False

        return True

    def determine_step(self):
        """Determine and adjust the selection step for the vial."""
        try:
            num_curves_this_step = len(self.gr_data[self.gr_data['time'] > self.last_step_change_time])
            
            if self.step_time >= self.min_step_time:
                last_gr_time = self.gr_data['time'].values[-1]
                last_gr = self.gr_data.tail(self.min_curves_per_step)['gr'].median()
                
                if (self.elapsed_time-last_gr_time) > self.growth_stalled_time:
                    self.decrease_step("GROWTH STALLED", last_gr_time)
                    self.eVOLVER.calc_growth_rate(self.vial, last_gr, self.elapsed_time) # calculate and log a growth rate from last dilution to now

                elif (last_gr < self.min_growthrate) and (num_curves_this_step >= self.min_curves_per_step):
                    self.decrease_step("LOW GROWTH RATE", last_gr_time)

                elif (last_gr > self.max_growthrate) and (num_curves_this_step >= self.min_curves_per_step):
                    self.increase_step("HIGH GROWTH RATE")
        except Exception as e:
            print(f"Vial {self.vial}: Error in step determination: {e}\n{traceback.format_exc()}")
            self.logger.error(f"Vial {self.vial}: Error in step determination: {e}\n{traceback.format_exc()}")

    def decrease_step(self, reason, gr_start):
        """Decrease the selection step for the vial."""
        if self.closest_step_index == 0:
            self.current_step = self.current_step * self.fold_decrease
            self.selection_status_message += f"[DECREASE] {self.fold_decrease}X to {self.current_step} {self.selection_units} [Already at or below minimum step] [{reason}] | "
        else:
            self.current_step = self.selection_steps[self.closest_step_index - 1]
            self.selection_status_message += f"[DECREASE] from {self.last_step} to {self.current_step} {self.selection_units} [{reason}] | "
        self.step_changed_time = self.elapsed_time
        self.logger.info(f"Vial {self.vial}: {self.selection_status_message}")

    def increase_step(self, reason):
        """Increase the selection step for the vial."""
        if (self.closest_step_index >= len(self.selection_steps) - 1) and (len(self.selection_steps) > 1):
            self.selection_status_message +=  f"[INCREASE FAILED] (Already at maximum step). Cannot increase further. | "
        else:
            if self.current_step < self.selection_steps[0]: # If the current step is below the minimum, increase to the minimum
                self.current_step = self.selection_steps[0]
            elif len(self.selection_steps) == 1: # If there is only one step, increase to that step
                self.current_step = self.selection_steps[0]
                return "" # No need to log the increase if there is only one step
            else:
                self.current_step = self.selection_steps[self.closest_step_index + 1]
            self.selection_status_message += f"[INCREASE] from {self.last_step} to {self.current_step} {self.selection_units} [{reason}] | "
            self.step_changed_time = self.elapsed_time
        self.logger.info(f"Vial {self.vial}: {self.selection_status_message}")

    ## FLUIDICS FUNCTIONS ##    
    def adjust_concentration(self, MESSAGE, time_out, VOLUME, lower_thresh, flow_rate, bolus_slow):
        """
        Adjust the concentration of the selection chemical in the vial based on the current experiment state.
        This method updates the current chemical concentration, optionally performs rescue dilutions if needed,
        and calculates the amount of selection chemical to add to maintain or reach a target concentration.

        Parameters:
        - MESSAGE: Fluidics command message for all vials.
        - time_out: Extra time to pump for efflux pumps
        - VOLUME: Volume of the vial in mL.
        - lower_thresh: Lower OD threshold for this vial.
        - flow_rate: Flow rate of ALL pumps in mL/s.
        - bolus_slow: Minimum bolus size for selection chemical addition.

        Returns:
        - MESSAGE: Fluidics command message for all vials, updated for this vial.
        """
        try:
            # Update the concentration of the selection chemical in the vial if there was a dilution event
            self.update_concentration()
            
            # Rescue Dilutions
            if self.rescue_dilutions and (self.current_step < self.last_step):
                return self.rescue_dilution(MESSAGE, lower_thresh, VOLUME, flow_rate, time_out)
            
            # Chemical Addition
            elif self.current_step > self.current_conc:
                try:
                    # Calculate amount of chemical to add to vial; only add if below target concentration and above lower OD threshold
                    if (np.median(self.OD_data[:,1]) > lower_thresh) and (self.current_step > 0):
                        calculated_bolus = (VOLUME * (self.current_conc - self.current_step)) / (self.current_step - self.stock_conc) # in mL, bolus size of stock to add
                        # calculated_bolus derived from concentration equation:: C_final = [C_a * V_a + C_b * V_b] / [V_a + V_b]

                        if calculated_bolus > self.max_selection_bolus: # prevent more than 5 mL added at one time to avoid overflows
                            calculated_bolus = 5
                            # Update current concentration because we are not bringing to full target conc
                            self.current_conc = ((self.stock_conc * calculated_bolus) + (self.current_conc * VOLUME)) / (calculated_bolus + VOLUME) 
                            self.logger.info(f'Vial {self.vial}: Selection chemical bolus too large (adding 5mL) | current concentration {round(self.current_conc, 3)} {self.selection_units} | current step {self.current_step}')
                        elif calculated_bolus < bolus_slow:
                            self.logger.info(f'Vial {self.vial}: Selection chemical bolus too small: current concentration {round(self.current_conc, 3)} {self.selection_units} | current step {self.current_step}')
                            # print(f'Vial {vial}: Selection chemical bolus too small: current concentration {round(current_conc, 3)} {selection_units} | current step {current_step}')
                            calculated_bolus = 0
                        else:
                            self.logger.info(f'Vial {self.vial}: Selection chemical bolus added, {round(calculated_bolus, 3)}mL | {self.current_step} {self.selection_units}')
                            self.current_conc = self.current_step

                        if calculated_bolus != 0 and not np.isnan(calculated_bolus):
                            time_in = round(calculated_bolus / float(flow_rate[self.vial + 32]), 2) # time to add bolus
                            MESSAGE[self.vial + 32] = str(time_in) # set the pump message
                        
                            fu.update_log(self.vial, 'slow_pump_log', self.elapsed_time, time_in, self.exp_dir)
                            self.selection_status_message += f'SELECTION CHEMICAL ADDED {round(calculated_bolus, 3)}mL | '

                    elif (np.median(self.OD_data[:,1]) < lower_thresh) and (self.current_step != 0):
                        self.logger.info(f'Vial {self.vial}: SKIPPED selection chemical bolus: OD {round(np.median(self.OD_data[:,1]), 2)} below lower OD threshold {lower_thresh}')
                        self.selection_status_message += f'SKIPPED SELECTION CHEMICAL - LOW OD {round(np.median(self.OD_data[:,1]), 2)} | '
                except Exception as e:
                    print(f"Vial {self.vial}: Error in Selection Chemical Addition Step: \n\t{e}\nTraceback:\n\t{traceback.format_exc()}")
                    self.logger.error(f"Vial {self.vial}: Error in Selection Chemical Addition Step: \n\t{e}\nTraceback:\n\t{traceback.format_exc()}")
            return MESSAGE
        
        except Exception as e:
            print(f"Vial {self.vial}: Error in Selection Fluidics Step: \n\t{e}\nTraceback:\n\t{traceback.format_exc()}")
            self.logger.error(f"Vial {self.vial}: Error in Selection Fluidics Step: \n\t{e}\nTraceback:\n\t{traceback.format_exc()}")
            return MESSAGE

    def update_concentration(self):
        """
        Update the concentration of the selection chemical in the vial if there was a dilution event.

        """
        last_dilution = fu.get_last_n_lines('pump_log', self.vial, 1, self.exp_dir)[0] # Load the last pump event
        last_dilution_time = last_dilution[0] # time of the last pump event

        # Calculate the dilution factor based off of proportion of OD change
        OD_times = self.OD_data[:, 0]
        if (last_dilution_time == OD_times[-(self.dilution_window+1)]) and (self.last_conc != 0): # Waiting until we have dilution_window length OD data before and after dilution 
            # Calculate current concentration of selection chemical
            OD_before = np.median(self.OD_data[:self.dilution_window, 1]) # Find OD before and after dilution
            OD_after = np.median(self.OD_data[-self.dilution_window:, 1])
            dilution_factor = OD_after / OD_before # Calculate dilution factor
            self.current_conc = self.last_conc * dilution_factor
            self.selection_status_message += f'DILUTION {round(dilution_factor, 3)}X | '

    def rescue_dilution(self, MESSAGE, lower_thresh, VOLUME, flow_rate, time_out):
        """
        Perform a dilution to rescue cells by lowering the selection level if cell density is above a specified threshold.
        This method checks if the maximum number of rescue dilutions has already been performed. If not, it calculates a dilution factor
        and adjusts the pump times to lower the selection to either a predefined rescue threshold or the previous selection step.
        """
        rescue_count = su.count_rescues(self.vial, self.exp_dir) # Determine number of previous rescue dilutions since last selection increase
        if self.rescue_dilutions and (rescue_count >= self.rescue_dilutions):
            self.logger.warning(f'Vial {self.vial}: SKIPPING RESCUE DILUTION | number of rescue dilutions since last selection increase ({rescue_count}) >= rescue_dilutions ({self.rescue_dilutions})')
            return MESSAGE

        elif self.rescue_dilutions and (np.median(self.OD_data[:,1]) > (lower_thresh*self.rescue_threshold)): # Make a dilution to rescue cells to lower selection level; however don't make one if OD is too low or we have already done the max number of rescues
            # Calculate the amount to dilute to reach the new selection level
            if self.last_step <= self.selection_steps[0]: # If the last step is at or below the minimum, dilute to the rescue threshold
                dilution_factor = self.rescue_threshold
            else:
                dilution_factor = self.current_step / self.last_conc # Otherwise dilute to the last step
            if dilution_factor < self.rescue_threshold:
                self.logger.warning(f'Vial {self.vial}: [RESCUE DILUTION] | dilution_factor: {round(dilution_factor, 3)} < {self.rescue_threshold}: setting to the rescue_threshold ({self.rescue_threshold}) | last step {self.last_step} | current step {self.current_step} {self.selection_units}')
                dilution_factor = self.rescue_threshold
            
            # Set pump time_in for dilution and log the pump event
            time_in = - (np.log(dilution_factor)*VOLUME)/flow_rate[self.vial] # time to dilute to the new selection level
            if np.isnan(time_in) or (time_in <= 0): # Check time_in for NaN
                self.logger.error(f'Vial {self.vial}: SKIPPING RESCUE DILUTION | time_in is {time_in}')
                print(f'Vial {self.vial}: SKIPPING RESCUE DILUTION | time_in is {time_in}')
            else: # Make a rescue dilution
                if time_in > 20: # Limit the time to dilute to 20
                    time_in = 20
                    dilution_factor = np.exp((time_in*flow_rate[self.vial])/(-VOLUME)) # Calculate the new dilution factor
                    print(f'Vial {self.vial}: [RESCUE DILUTION] | Unable to dilute to {self.current_step} {self.selection_units} (> 20 seconds pumping) | Diluting by {round(dilution_factor, 3)} fold')
                    self.logger.info(f'Vial {self.vial}: [RESCUE DILUTION] | Unable to dilute to {self.current_step} {self.selection_units} (> 20 seconds pumping) | Diluting by {round(dilution_factor, 3)} fold')
                else:
                    print(f'Vial {self.vial}: [RESCUE DILUTION] | dilution_factor: {round(dilution_factor, 3)}')
                    self.logger.info(f'Vial {self.vial}: [RESCUE DILUTION] | dilution_factor: {round(dilution_factor, 3)}')
            
                time_in = round(time_in, 2)
                MESSAGE[self.vial] = str(time_in) # influx pump
                MESSAGE[self.vial + 16] = str(round(time_in + time_out,2)) # efflux pump
                fu.update_log(self.vial, 'pump_log', self.elapsed_time, time_in, self.exp_dir)
                self.selection_status_message += f'[RESCUE DILUTION] | '
                return MESSAGE

if __name__ == '__main__':
    print('Please run eVOLVER.py instead')
