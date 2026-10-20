"""Double-click launcher for the logicsynth GUI (.pyw runs without a console window on Windows)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from logicsynth.gui import main

sys.exit(main())
