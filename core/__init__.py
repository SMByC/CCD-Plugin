import os
import sys

module_path = os.path.abspath(".")
if module_path not in sys.path:
    sys.path.insert(0, module_path)
