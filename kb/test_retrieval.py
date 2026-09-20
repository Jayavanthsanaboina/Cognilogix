"""
Try the retrieval layer by hand, including non-English queries.

Run from the project root:   python -m kb.test_retrieval

Have a native speaker check the Hindi/Telugu sentences before you use them in a demo.
"""
from .retriever import KnowledgeBase

QUERIES = [
    ("English",  "AC is not cooling and there is ice on the pipe"),
    ("English",  "generator does not start after power cut"),
    ("Hinglish", "AC thanda nahi kar raha, hawa bahut kam aa rahi hai"),
    ("Hindi",    "जनरेटर बिजली जाने के बाद चालू नहीं हो रहा है"),
    ("Hindi",    "लिफ्ट का दरवाजा पूरा बंद नहीं हो रहा है"),
    ("Telugu",   "ఏసీ చల్లబడటం లేదు"),
    ("Telugu",   "లిఫ్ట్ డోర్ సరిగ్గా మూసుకోవడం లేదు"),
]


def main():
    kb = KnowledgeBase()
    print(f"Knowledge base size: {kb.count()} cases\n")
    for lang, query in QUERIES:
        print(f"[{lang}] {query}")
        for case in kb.search(query, k=3):
            print(f"   {case['similarity']:.3f}  {case['record_id']}  {case['equipment_type']}  ->  {case['root_cause']}")
        print()


if __name__ == "__main__":
    main()