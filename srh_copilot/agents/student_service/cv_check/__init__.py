"""CV Check logic, ported from the CV Optimizer Agent (teammate's project).

Pure functions only, no I/O, no LLM client, no framework imports. The task in
tasks/cv_check.py wires them to the platform's injected services:

    extractor.py    PDF/DOCX bytes -> clean text, font-size based name detection
    language.py     EN / DE detection
    anonymiser.py   four-layer PII removal before text leaves the server
    ats_checker.py  rule-based ATS compatibility score and issues
    report.py       LLM findings + ATS result -> markdown for the chat UI
"""
