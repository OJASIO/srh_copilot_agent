# data/

- `raw/<agent_id>/<collection>/`  knowledge files, one subfolder per collection
- `processed/`                    derived artefacts (the prototype vector index); rebuilt by
                                  `python scripts/ingest.py --agent <agent_id>`, never edited by hand

Everything in a collection folder can be quoted to students. Folders therefore hold curated markdown,
not the original internal documents. Original PDFs stay outside the repository (see `.gitignore`).

## Student Service collections

| Collection | Used by | Content |
|---|---|---|
| `scholarship` | Scholarship Information task | Responsible offices per scholarship (hotline FAQ section 8), amounts, deadlines and eligibility (SRH financing page), Deutschlandstipendium, BAfoeG, loans, discounts |
| `general` | not used yet (stored for a future "General questions" task) | Curated student-facing hotline FAQ, leave of absence and programme/campus change processes, Student Service website pages, apostille process, Student Service summary |
| `cv_check` | nothing (placeholder) | CV Check works from prompts and rules; Career Service CV guidelines would go into the review prompt |

## Rules for adding knowledge

1. Curate before adding. Remove internal-only notes, staff names, internal phone numbers and bank details.
   The hotline FAQ contains all four; `general/hotline_faq_students.md` is its student-facing version.
2. Start each file with its source and a date ("Stand" or "retrieved"), so outdated content is visible.
3. Replace outdated files instead of adding a second version; two versions of a fact give contradicting answers.
4. Prefer text over diagrams: only the text of a PDF is read, flowcharts and images are ignored.
5. Re-run ingestion after any change. A new collection folder also needs to be added to the task that
   should search it.

## Sources used (September 2026)

| Source | Stand | Where it went |
|---|---|---|
| FAQ Avaya Hotline StS (internal) | 31.03.2026 | `scholarship/srh_scholarships_overview.md` (section 8, 9), `general/hotline_faq_students.md` (curated) |
| Prozesssteckbrief Beurlaubung | Jan 2026 | `general/process_leave_of_absence.md` |
| Prozesssteckbriefe Studiengangs-/Standortwechsel (2) | Jan 2026 | `general/process_programme_campus_change.md` |
| Student Service Summary 2026 (internal) | 2026 | `general/student_service_summary.md` |
| srh-university.de: Financing, Deutschlandstipendium | retrieved 27.09.2026 | `scholarship/srh_financing_website.md` |
| srh-university.de: Student Service, Good to know | retrieved 27.09.2026 | `general/student_service_website.md` |
| mwk.baden-wuerttemberg.de: Apostillen | retrieved 27.09.2026 | `general/apostille.md` |
| AC5 Handbuch (internal staff manual) | 10.04.2026 | not used: staff software manual, screenshots may show student records |

## Changes after evaluation

- 27.09.2026: `scholarship/srh_financing_website.md`, "how to apply" written as numbered steps that say the
  student contacts the study advisor first (the in-house evaluation answer #6 had it the other way round).

## Open points

- Hamm campus phone: the hotline FAQ lists "+49 92381 9291121", which looks like a typo (Hamm's area code is 02381). Left out until confirmed.
- International Office scholarships (STIBET, Erasmus+, PROMOS, BaWue, HAW.International): amounts and deadlines are not in any source yet; the assistant refers these questions to the International Office.
- Semester ticket price for winter semester 2026/27 and the re-entry process were still open in the sources.
