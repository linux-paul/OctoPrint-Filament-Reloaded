# coding=utf-8
from __future__ import absolute_import

import octoprint.plugin
from octoprint.events import Events
import RPi.GPIO as GPIO
from time import sleep
from flask import jsonify


class FilamentReloadedPlugin(octoprint.plugin.StartupPlugin,
                             octoprint.plugin.ShutdownPlugin,
                             octoprint.plugin.EventHandlerPlugin,
                             octoprint.plugin.TemplatePlugin,
                             octoprint.plugin.SettingsPlugin,
                             octoprint.plugin.BlueprintPlugin):

    def __init__(self):
        self.triggered = 0
        self.paused = 0
        self.printing = 0
        self.filload = 0
        self.eventenabled = 0

    def initialize(self):
        self._logger.info("Running RPi.GPIO version '{0}'".format(GPIO.VERSION))
        if GPIO.VERSION < "0.6":       # Need at least 0.6 for edge detection
            raise Exception("RPi.GPIO must be greater than 0.6")
        GPIO.setwarnings(False)        # Disable GPIO warnings


    @octoprint.plugin.BlueprintPlugin.route("/status", methods=["GET"])
    def check_status(self):
        status = "-1"
        if self.sensor_enabled():
            status = "0" if self.no_filament() else "1"
        return jsonify(status=status)

    @property
    def pin(self):
        return int(self._settings.get(["pin"]))

    @property
    def bounce(self):
        return int(self._settings.get(["bounce"]))

    @property
    def switch(self):
        return int(self._settings.get(["switch"]))

    @property
    def mode(self):
        return int(self._settings.get(["mode"]))

    @property
    def no_filament_gcode(self):
        return str(self._settings.get(["no_filament_gcode"])).splitlines()

    @property
    def pause_print(self):
        return self._settings.get_boolean(["pause_print"])

    @property
    def send_gcode_only_once(self):
        return self._settings.get_boolean(["send_gcode_only_once"])

    def _setup_sensor(self):
        if self.sensor_enabled():
            self._logger.info("Setting up sensor.")
            if self.mode == 0:
                self._logger.info("Using Board Mode")
                GPIO.setmode(GPIO.BOARD)
            else:
                self._logger.info("Using BCM Mode")
                GPIO.setmode(GPIO.BCM)
            self._logger.info("Filament Sensor active on GPIO Pin [%s]"%self.pin)
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        else:
            self._logger.info("Pin not configured, won't work unless configured!")

    def on_after_startup(self):
        self._logger.info("Filament Sensor Reloaded started")
        self._setup_sensor()

    def get_settings_defaults(self):
        return dict(
            pin     = -1,   # Default is no pin
            bounce  = 250,  # Debounce 250ms
            switch  = 0,    # Normally Open
            mode    = 0,    # Board Mode
            no_filament_gcode = '',
            pause_print = True,
            send_gcode_only_once = False, # Default set to False for backward compatibility
        )

    def on_settings_save(self, data):
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        self._setup_sensor()

    def sensor_enabled(self):
        return self.pin != -1

    def no_filament(self):
        return GPIO.input(self.pin) != self.switch

    def get_template_configs(self):
        return [dict(type="settings", custom_bindings=False)]

    def on_event(self, event, payload):
        # Early abort in case of out ot filament when start printing, as we
        # can't change with a cold nozzle
        if event is Events.PRINT_STARTED and self.no_filament():
            self._logger.info("Printing aborted: no filament detected!")
            self._printer.cancel_print()
        # Enable sensor
        elif event in (Events.PRINT_STARTED):
            self._logger.info("%s: Enabling filament sensor." % (event))
            if self.sensor_enabled() and not self.eventenabled:
                GPIO.add_event_detect(
                    self.pin, GPIO.BOTH,
                    callback=self.sensor_callback,
                    bouncetime=self.bounce
                )
                self.eventenabled = 1
            self.filload = 1
            self.triggered = 0
            self.printing = 1
            self.paused = 0
        # Disable sensor
        elif event in (Events.PRINT_RESUMED):
            self._logger.info("Resuming Print...")
            self.printing = 1
            self.paused = 0
            self.triggered = 0

        elif event in (Events.PRINT_DONE, Events.PRINT_FAILED, Events.PRINT_CANCELLED, Events.ERROR):
            self.triggered = 0
            self.printing = 0
            self.paused = 0
            if self.eventenabled :
                self._logger.info("%s: Disabling filament sensor." % (event))
                GPIO.remove_event_detect(self.pin)
                self.eventenabled = 0

        elif event is Events.PRINT_PAUSED:
            self.printing = 0
            self.paused = 1
            self.triggered = 0  # reset triggered state

    def sensor_callback(self, _):
        sleep(self.bounce/1000)

        self.check_change()

        # If we have previously triggered a state change we are still out 
        # of filament. Log it and wait on a print resume or a new print job.
        if self.triggered and self.send_gcode_only_once:
            self._logger.info("Sensor triggert but no change in state.")
            return

        if self.no_filament() and self.printing:
            self._logger.info("Out of filament!")
            self.filload = 0
            if self.send_gcode_only_once:
                self._logger.info("Sending GCODE only once...")
                self.triggered = 1
            if self.pause_print:
                self._logger.info("Pausing print...")
                self._printer.pause_print()
                while not self.paused:
                    sleep(1) 
            if self.no_filament_gcode:
                self._logger.info("Sending out of filament GCODE")
                self._printer.commands(self.no_filament_gcode)

        elif ((self.paused and self.pause_print) or not self.pause_print) or (not self.no_filament() and self.printing):
            self._logger.info("Filament detected!")
            self.filload = 1
            if self.send_gcode_only_once:
                self.triggered = 1


    def check_change(self):
        if (self.filload and self.no_filament()) or (not self.filload and not self.no_filament()):
            self.triggered = 0

    def get_update_information(self):
        return dict(
            octoprint_filament=dict(
                displayName="Filament Sensor Reloaded",
                displayVersion=self._plugin_version,

                # version check: github repository
                type="github_release",
                user="linux-paul",
                repo="Octoprint-Filament-Reloaded",
                current=self._plugin_version,

                # update method: pip
				pip="https://github.com/linux-paul/OctoPrint-Filament-Reloaded/archive/{target_version}.zip"
            )
        )

    def on_shutdown(self):
        GPIO.cleanup()

__plugin_name__ = "Filament Sensor Reloaded"
__plugin_version__ = "1.0.2"

def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = FilamentReloadedPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
}
