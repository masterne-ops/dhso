#!/usr/bin/env python3
"""🎯 场景化专项 — M13 三大专项攻坚之一"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _focus_ui import render_focus_page  # noqa: E402

st.set_page_config(page_title='场景化专项', layout='wide')
render_focus_page('场景化')
