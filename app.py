"""
Alpine Analytics — Entry Point
"""

import streamlit as st

st.set_page_config(
    page_title="Alpine Analytics",
    page_icon="⛷️",
    layout="wide",
)

pg = st.navigation([
    st.Page("pages/0_Home.py",            title="Alpine Analytics", default=True),
    st.Page("pages/1_Athlete.py",         title="Athlete Profile"),
    st.Page("pages/2_Race_Results.py",    title="Race Results"),
    st.Page("pages/3_Course_Explorer.py", title="Course Explorer"),
    st.Page("pages/4_Race_Simulator.py",  title="Race Simulator"),
    st.Page("pages/5_Recruiting_Board.py", title="Recruiting Board"),
])
pg.run()
