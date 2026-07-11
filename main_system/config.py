"""Load the shared config once for the main system."""
import os

import yaml

_HERE = os.path.dirname(__file__)
with open(os.path.join(_HERE, "..", "config", "config.yaml")) as _fh:
    CFG = yaml.safe_load(_fh)
