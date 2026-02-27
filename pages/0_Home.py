"""
Alpine Analytics — Home / Overview
"""

import streamlit as st

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("# Alpine Analytics")
st.markdown("##### Field-relative performance intelligence for FIS alpine ski racing")
st.markdown("---")

# ── Navigation cards ──────────────────────────────────────────────────────────
c1, c2, c3 = st.columns(3, gap="large")

with c1:
    st.markdown("### Athlete Profile")
    st.markdown(
        "Search any athlete to explore their field-relative performance across every race, "
        "current momentum trend, course-type strengths and weaknesses, DNF resilience, "
        "and the venues where they historically perform best."
    )
    st.page_link("pages/1_Athlete.py", label="Open Athlete Profile →")

with c2:
    st.markdown("### Race Results")
    st.markdown(
        "Break down any race by the numbers: full field Z-Score rankings, "
        "who overperformed and underperformed relative to their expected level, "
        "field depth, and how the winner compared to historical results at that venue."
    )
    st.page_link("pages/2_Race_Results.py", label="Open Race Results →")

with c3:
    st.markdown("### Course Explorer")
    st.markdown(
        "Analyze any venue: Hill Difficulty Index score, course setter tendencies, "
        "how field quality and competitiveness have shifted across editions, "
        "and which other courses are most physically similar."
    )
    st.page_link("pages/3_Course_Explorer.py", label="Open Course Explorer →")

st.markdown("---")

# ── Value prop ─────────────────────────────────────────────────────────────────
st.markdown("## Why Not Just Use FIS Points?")
st.markdown(
    """
FIS points are the official skill rating in alpine ski racing — and they serve their purpose.
After each race, a skier earns race points based on their time gap to the winner. A penalty
is added to account for field quality. FIS then takes the **average of the athlete's two best
results over the past 18 months** in each discipline. Lower is better — elite World Cup athletes
push into single digits; beginners start at 990.

That system is well-designed for ranking and seeding. But it was built to answer one question:
*How good is this athlete overall?* It was not built to answer the questions that make racing
analysis genuinely interesting.
"""
)

col_a, col_b = st.columns(2, gap="large")

with col_a:
    st.markdown(
        """
**What FIS points tell you:**
- Career ceiling — the best two races in the last 18 months
- Overall ranking and seeding position
- Rough gauge of competitive level

**What FIS points cannot tell you:**
- Whether an athlete is peaking or fading *right now*
- How they actually performed vs. the specific field on race day
- Whether they excel on certain course types and struggle on others
- How they respond after a DNF — do they come back strong or chain non-finishes?
- How consistent they are across all races, not just their best two
- Which courses historically bring out their best
        """
    )

with col_b:
    st.markdown(
        """
**The core limitation:** FIS points count only your best two results. An athlete who posts
two elite performances and DNFs six other races looks identical to one who finishes cleanly
in every start. That distinction matters enormously when you are trying to assess readiness,
identify momentum, or understand a racer's true reliability.

**The field-quality problem:** A 10th place in a 70-person World Cup field at Kitzbühel is
not the same as a 10th place in a 30-person regional race. FIS applies a race penalty to
partially adjust, but the adjustment is opaque, bounded, and does not normalize across the
full field distribution. Z-Score does.

**The timing problem:** FIS points are calculated from results up to 18 months old.
An athlete who peaked last season and is currently declining has identical FIS points to
one who is right now on a six-race winning streak against elite competition.
        """
    )

st.markdown("Alpine Analytics addresses all of these gaps by computing field-normalized metrics across every result in every race — not just the best two.")

st.markdown("---")

# ── Key concepts ──────────────────────────────────────────────────────────────
st.markdown("## Key Metrics Explained")

k1, k2 = st.columns(2, gap="large")

with k1:
    with st.container(border=True):
        st.markdown("#### Race Z-Score — The Core Unit")
        st.markdown(
            """
            Every result expressed as **standard deviations above or below the field average**
            for that specific race.

            - **+1.0** = finished one standard deviation above the average competitor that day — a genuinely strong result
            - **0.0** = exactly at the field mean — average for who showed up
            - **−1.0** = one standard deviation below the field — an underperformance relative to the field

            Z-Score is **field-normalized**, meaning it corrects for field size, competition level,
            and venue. A Z of +0.8 at a World Cup and a Z of +0.8 at a Europa Cup represent equally
            dominant performances relative to each day's actual competition. This allows you to
            compare results across venues, seasons, and field compositions on a single consistent scale.

            A career average Z above 0 means the athlete beats the field more often than not —
            the clearest single indicator of whether an athlete is above or below their competitive median.

            **How it is calculated:**

            For each race, collect every finisher's FIS race points. Compute the field mean and
            standard deviation. Then for each athlete:

            > **Z = (Field Mean FIS − Athlete FIS) / Field Std Dev**

            Because lower FIS points mean a better result, the formula subtracts the athlete's
            points *from* the mean — so a top finisher with a low FIS point total produces a
            positive Z. An athlete at exactly the field mean gets Z = 0. When all finishers
            post identical times (zero standard deviation), Z is set to 0 by convention.
            Only athletes with a valid FIS points result are included in each race's field calculation —
            DNFs, DSQs, and DNS entries are excluded from the distribution.
            """
        )

    with st.container(border=True):
        st.markdown("#### Strokes Gained — Margin, Not Just Direction")
        st.markdown(
            """
            Borrowed from professional golf analytics. Strokes Gained measures
            **how many FIS points the athlete gained or lost relative to the field average** in each race.

            - A **positive** result means the athlete outperformed the field average and gained ground
            - A **negative** result means they lost ground relative to the average competitor

            Where Z-Score tells you *direction* (beat or lost to the field), Strokes Gained tells you
            *magnitude* — by how much. Accumulated over a career, it reveals whether an athlete tends
            to edge out the field by small margins or deliver dominant performances, and whether their
            losses are shallow or steep. It is the most complete single-race performance measure in the platform.
            """
        )

    with st.container(border=True):
        st.markdown("#### Momentum — Current Form, Not Historical Average")
        st.markdown(
            """
            A rolling average of recent Z-Scores that tracks whether an athlete's
            **form is currently rising or falling** — independent of their career average.

            - **Rising above zero** = athlete is in form; recent starts are consistently above the field average
            - **Falling toward or below zero** = form is cooling; recent starts are trending toward or below average
            - **Positive value** = on a current run of above-average races

            FIS points capture career ceiling. Momentum captures the present. An athlete ranked 15th
            on FIS points with sharply rising momentum is often a more dangerous competitor heading
            into a race block than one ranked 8th with declining momentum. This is the metric most
            relevant to predicting near-term results.
            """
        )

with k2:
    with st.container(border=True):
        st.markdown("#### Hill Difficulty Index (HDI)")
        st.markdown(
            """
            A composite **0–100 score** measuring how physically and technically demanding a course
            is — based entirely on course and race characteristics, not on who wins.

            | Component | Weight | What it captures |
            |---|---|---|
            | DNF Rate | 40% | Attrition — how often athletes fail to finish |
            | Vertical Drop | 20% | Elevation change, start to finish |
            | Winning Time | 20% | Duration of exposure on the hill |
            | Gate Count | 10% | Technical complexity |
            | Start Altitude | 10% | High altitude adds exposure and physical demand |

            Scores are normalized **within each discipline** — a Downhill HDI of 75 and a Slalom
            HDI of 75 both mean "among the harder courses in their event," but the two numbers
            are not comparable to each other. Use HDI to evaluate courses within a discipline,
            not across them.
            """
        )

    with st.container(border=True):
        st.markdown("#### Course Traits — Where an Athlete Wins and Loses")
        st.markdown(
            """
            An analysis of how an athlete's performance systematically varies across different
            **course characteristic ranges** — gate count, altitude, vertical drop, winning time,
            DNF rate, and starting bib position.

            Courses are grouped into five bins (quintiles, lowest to highest) for each characteristic.
            For each bin, the athlete's average Z-Score is compared to their career average.
            A positive delta means they consistently outperform their own standard in that range.
            A negative delta means that characteristic is a relative weakness.

            This is not noise — these patterns are computed across full careers and reflect genuine
            structural tendencies. An athlete who is +0.4 Z in the highest gate-count bin but −0.3
            in the lowest is a technical specialist who struggles on open, fewer-gate courses.
            That information is invisible in FIS points.
            """
        )

    with st.container(border=True):
        st.markdown("#### Consistency & DNF Resilience")
        st.markdown(
            """
            Two reliability dimensions that no points system captures:

            **Consistency** — The coefficient of variation (CV) of Z-Scores across an athlete's career.
            Low CV = predictable, reliable delivery. High CV = the same athlete who dominates one race
            may fall well below average in the next. When evaluating athletes for sponsorship, team
            selection, or pre-race analysis, consistency often matters as much as peak performance.

            **DNF Resilience** — When an athlete fails to finish, two questions follow: How likely are
            they to DNF again in their very next race? And when they do come back and finish, do they
            perform above or below the field average? These metrics reveal mental and physical durability
            under adversity — qualities entirely absent from the FIS points calculation.
            """
        )

st.markdown("---")

# ── How to read the numbers ────────────────────────────────────────────────────
st.markdown("## Reference: How to Read the Numbers")
st.markdown(
    """
    | Metric | Strong | Solid | Concerning |
    |---|---|---|---|
    | Single-race Z-Score | Above +0.5 | −0.2 to +0.5 | Below −0.5 repeatedly |
    | Season average Z-Score | +0.3 or higher | 0.0 to +0.3 | Negative across a full season |
    | Momentum heading into a race | Rising, above 0 | Flat near 0 | Declining through the back half of a season |
    | Hill Difficulty Index | 70–100 = genuinely demanding | 30–70 = moderate | Below 30 = technically straightforward |
    | Course DNF Rate | Below 10% = clean, finishable course | 10–25% | Above 25% = high attrition |
    | Athlete Re-DNF Rate | Below 15% | 15–30% | Above 35% = vulnerability after non-finishes |
    | Bounce-Back Z (after DNF) | Above 0 = returns above field average | Near 0 | Negative = typically underperforms on return |

    **Key caveats:**
    - Z-Scores are only meaningful **within** a discipline. Comparing a Slalom Z to a Downhill Z is not valid — field compositions differ fundamentally.
    - Fields smaller than ~20 athletes introduce statistical instability. A single strong or weak result can shift averages significantly.
    - Course Similarity is computed within the same discipline. It reflects physical hill characteristics — not weather, snow, or course-setter style.
    - All data covers the **2020 season onward**, when the current FIS points formula was introduced. Pre-2020 results use a different calculation basis.
    """
)

st.markdown("---")

# ── Data source ───────────────────────────────────────────────────────────────
st.markdown("## Data")
st.markdown(
    """
    All data is sourced from **FIS** (Fédération Internationale de Ski et des Sports de Glisse),
    the international governing body of alpine ski racing. FIS publishes official race results,
    athlete FIS points, and course homologation records for every sanctioned World Cup,
    World Championship, and Europa Cup event in Slalom, Giant Slalom, Super-G, Downhill,
    and Alpine Combined.

    Derived metrics — Z-scores, Strokes Gained, Momentum, HDI, Course Similarity,
    Course Traits, and Consistency — are computed by the Alpine Analytics pipeline from
    raw FIS results and course records. The database is updated after each race weekend.
    """
)

st.markdown("---")

# ── Footer ────────────────────────────────────────────────────────────────────
left, right = st.columns([3, 1])
with left:
    st.caption("Data sourced from FIS · 2020 season onward · Updated after each race weekend")
with right:
    st.caption("Created by Finnbahr Malcolm")
