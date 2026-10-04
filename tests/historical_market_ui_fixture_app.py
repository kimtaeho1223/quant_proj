import os

import streamlit as st

from quantdesk.historical_market_ui import render_historical_market


def fake_launcher(job_id, db_path, raw_root, service_key):
    st.session_state['launched_job'] = job_id
    st.session_state['launch_key_length'] = len(service_key)


render_historical_market(
    os.environ['QUANTDESK_MARKET_DB'],
    os.environ['QUANTDESK_RAW_ROOT'],
    lifecycle_raw_root=os.environ.get('QUANTDESK_LIFECYCLE_RAW_ROOT'),
    investability_raw_root=os.environ.get('QUANTDESK_INVESTABILITY_RAW_ROOT'),
    launcher=fake_launcher,
)
