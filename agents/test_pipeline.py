"""
Run complaints through the full agent pipeline and print the result.

  python -m agents.test_pipeline                      (runs the sample complaints)
  python -m agents.test_pipeline "your own complaint" (runs just that one)

Groq's free tier has per-minute limits, so the samples run with a short pause.
"""
import sys
import time

from .pipeline import diagnose_complaint

PAUSE_SECONDS = 10

SAMPLES = [
    "AC in Block C is not cooling and there is ice on the copper pipe",
    "Generator did not start during the power cut in the hostel",
    "Lift door is not closing properly in the library",
    "Burning smell and sparks from the switch board in the lab",
    "జనరేటర్ కరెంట్ పోయిన తర్వాత స్టార్ట్ అవ్వడం లేదు",
    "जनरेटर बिजली जाने के बाद चालू नहीं हो रहा है",
    "AC thanda nahi kar raha, hawa bahut kam aa rahi hai",
    "Something feels wrong in the building",
]


def show(complaint):
    result = diagnose_complaint(complaint)
    print("=" * 78)
    print("COMPLAINT :", complaint)
    print(f"LANGUAGE  : {result.get('language')}   EQUIPMENT: {result.get('equipment_type')}"
          f"   SAFETY FLAG: {result.get('safety_flag')}")
    print("ENGLISH   :", result.get("english_text"))

    print("\nSIMILAR CASES")
    for case in result["cases"][:4]:
        print(f"  {case['similarity']:.3f}  {case['record_id']}  {case['root_cause']}")

    diagnosis = result["diagnosis"]
    print("\nDIAGNOSIS" + ("   ** INSUFFICIENT EVIDENCE **" if diagnosis["insufficient_evidence"] else ""))
    for r in diagnosis["ranked"]:
        print(f"  {r['confidence']:.0%}  {r['cause']}")
        print(f"       {r['reasoning']}")
    if diagnosis.get("check_first"):
        print("  check first:", diagnosis["check_first"])

    rec = result["recommendation"]
    print("\nRECOMMENDATION")
    if rec.get("action"):
        print("  action:", rec["action"])
    for i, step in enumerate(rec["fix_steps"], 1):
        print(f"  {i}. {step}")
    if rec["cost_inr"]:
        c, h = rec["cost_inr"], rec["downtime_hours"]
        print(f"  cost: Rs {c['min']}-{c['max']} (median {c['median']})   "
              f"time: {h['min']}-{h['max']} h (median {h['median']})")
    print(f"  urgency: {rec['urgency']}   safety-critical: {rec['safety_critical']}")

    explanation = result["explanation"]
    print("\nEXPLANATION")
    print(" ", explanation["summary"])
    for step in explanation["reasoning_chain"]:
        print("   -", step)
    if explanation["safety_note"]:
        print("  SAFETY:", explanation["safety_note"])

    print("\nAGENT TRACE")
    for t in result["trace"]:
        print(f"  {t['agent']:<9} {t['ms']:>5} ms  {t['summary']}")
    print()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # so Hindi/Telugu print correctly on Windows
    except Exception:
        pass
    complaints = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else SAMPLES
    for i, complaint in enumerate(complaints):
        show(complaint)
        if i < len(complaints) - 1:
            time.sleep(PAUSE_SECONDS)


if __name__ == "__main__":
    main()