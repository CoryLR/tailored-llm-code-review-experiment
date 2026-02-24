
# Related Work Summaries

## Code Review Practices and Variations

**Bacchelli & Bird, "Expectations, Outcomes, and Challenges of Modern Code Review," ICSE 2013.**
- Summary: Studied code review at Microsoft through observation, interviews, and surveys. Found that defects account for only ~14% of review comments; the majority address conventions, knowledge transfer, and alternative solutions.
- Relation: Establishes that most review feedback is context-dependent and non-defect, motivating project-specific adaptation.

**Sadowski et al., "Modern Code Review: A Case Study at Google," ICSE-SEIP 2018.**
- Summary: Investigated code review at Google across 9 million reviewed changes. Documents the "readability" certification system requiring language-specific style expertise before a developer can approve changes.
- Relation: Google's readability process is the human analogue of project-specific prompt tuning: institutionalized, context-specific review norms.

**Rigby & Bird, "Convergent Contemporary Software Peer Review Practices," ESEC/FSE 2013.**
- Summary: Studied review practices across Google, Microsoft, AMD, Lucent, and six OSS projects. Found convergence on process parameters (two reviewers, small changes, quick turnaround) but divergence in review substance, with knowledge sharing varying 66-150% by project.
- Relation: The convergence/divergence split is central: process is universal but review content varies by project, which is what we propose to capture.

**Czerwonka et al., "Code Reviews Do Not Find Bugs," ICSE 2015.**
- Summary: Experience report from Microsoft finding only ~15% of review comments indicate defects while ~50% are maintenance-related. Argues the "find bugs" framing of code review is misleading.
- Relation: Reinforces that conventions and maintenance dominate review output, and that effective reviews require context-specific expertise.

**Singh et al., "Evaluating How Static Analysis Tools Can Reduce Code Review Effort," VL/HCC 2017.**
- Summary: Measured overlap between PMD static analysis warnings and human reviewer comments across 274 comments from 92 GitHub PRs. PMD overlapped with only ~16% of reviewer comments.
- Relation: Quantifies the gap between static analysis and human review; the remaining ~84% (design, conventions, maintainability) is where LLMs can add value.

## LLM-Based Automated Code Review

**Tufano et al., "On Learning Meaningful Code Changes Via Neural Machine Translation," ICSE 2019.**
- Summary: Pioneered mining Gerrit code review repositories to train neural models on code changes, achieving 9-17% perfect predictions on small methods via NMT.
- Relation: Established the paradigm of learning from code review data that subsequent work (CodeReviewer, fine-tuning approaches) built upon.

**Li et al., "Automating Code Review Activities by Large-Scale Pre-Training" (CodeReviewer), ESEC/FSE 2022.**
- Summary: Pre-trained encoder-decoder model with four code-review-specific tasks, trained on 1,000+ OSS repos across 9 languages. Established the standard benchmark for code review automation.
- Relation: The CodeReviewer benchmark is the evaluation platform most subsequent work builds on; our project scrapes its own data to enable full codebase access.

**Pornprasit & Tantithamthavorn, "Fine-Tuning and Prompt Engineering for LLM-based Code Review Automation," IST 2024.**
- Summary: Systematic comparison of 12 fine-tuning and prompting strategies across GPT-3.5 and Magicoder. Fine-tuned GPT-3.5 achieves 73-74% higher exact match; few-shot beats zero-shot by 46-659% when fine-tuning is unavailable.
- Relation: The only paper comparing fine-tuning vs. prompting for code review, but all prompts are generic; project-specificity is never varied.

**Guo et al., "Exploring the Potential of ChatGPT in Automated Code Refinement," ICSE 2024.**
- Summary: Found that ChatGPT with generic prompts surpassed the fine-tuned CodeReviewer (22.78 vs. 15.50 exact match, 76.44 vs. 62.88 BLEU-4) on the CodeReviewer benchmark.
- Relation: Establishes the "generic large LLM" baseline; raises the question of whether project-specific prompts can push performance further.

**Sun et al., "BitsAI-CR: Automated Code Review via LLM in Practice," FSE 2025.**
- Summary: ByteDance's two-stage framework combining a taxonomy of 219 org-specific review rules with LLM-based precision verification. Achieves 75.0% precision across 12,000+ weekly active users.
- Relation: Closest to comparing org-specific vs. generic, but confounds prompt specificity with architecture changes, fine-tuning, and curated rules.

**Cihan et al., "Automated Code Review in Practice," ICSE-SEIP 2025.**
- Summary: Industrial study at Beko where 238 practitioners across 10 projects used an AI-assisted review tool. 73.8% of automated comments were resolved, but effectiveness varied significantly by project.
- Relation: Observes per-project variation in LLM review effectiveness but deploys the same generic tool uniformly; this variation is what we propose to address.

**Zhang et al., "LAURA: Enhancing Code Review Generation with Context-Enriched Retrieval-Augmented LLM," ASE 2025.**
- Summary: Retrieval-augmented framework integrating review exemplar retrieval, context augmentation, and systematic guidance. Generates completely correct or helpful comments in 42% of cases with ChatGPT-4o.
- Relation: Prompt-level adaptation via exemplar retrieval, but never ablates same-project vs. cross-project retrieval, so the effect of project-specificity is unknown.

**Nashaat & Miller, "CodeMentor: Towards Efficient Fine-Tuning of Language Models With Organizational Data for Automated Software Review," TSE 2024.**
- Summary: Three-phase pipeline (SFT, self-instruct data augmentation, RLHF with domain experts) for fine-tuning LLMs on organizational code review data. Achieves up to 43.4% improvement in review generation.
- Relation: Closest related work; achieves org-specificity through fine-tuning requiring gradient access, while our approach adapts via prompts only.

**Tantithamthavorn et al., "RovoDev Code Reviewer," ICSE-SEIP 2026.**
- Summary: Enterprise LLM code review tool deployed at Atlassian across 1,900+ repositories using zero-shot prompting. Achieves 38.7% code resolution rate and 30.8% reduction in PR cycle time.
- Relation: Demonstrates generic prompt-based review works at scale; our work tests whether project-specific optimization can push beyond this baseline.

## Project-Specific Adaptation

**Zimmermann et al., "Cross-Project Defect Prediction," ESEC/FSE 2009.**
- Summary: Ran 622 cross-project defect prediction combinations across 12 applications. Only 3.4% yielded acceptable performance; domain or process similarity alone does not enable transfer.
- Relation: Canonical citation that cross-project transfer fails in SE, motivating per-project optimization.

**Shimagaki et al., "A Study of the Quality-Impacting Practices of Modern Code Review at Sony Mobile," ICSE-C 2016.**
- Summary: Tested whether OSS-derived code review metrics predict defect-proneness at Sony Mobile. Generic metrics failed; only context-aware metrics accounting for Sony's practices were effective.
- Relation: Most direct evidence that code review quality measurement requires project-specific adaptation, implying review generation should too.

**Ahmed & Devanbu, "Few-Shot Training LLMs for Project-Specific Code-Summarization," ASE 2022.**
- Summary: Showed that 10 project-specific few-shot examples enable GPT Codex to surpass models fine-tuned on 24K-251K generic examples for code summarization. Same-project examples yielded 12.56% improvement over cross-project.
- Relation: Strongest evidence that project-specific in-context learning beats generic fine-tuning for SE tasks.

**Nashid et al., "Retrieval-Based Prompt Selection for Code-Related Few-Shot Learning" (CEDAR), ICSE 2023.**
- Summary: Retrieval-based technique (BM25 + embedding similarity) for selecting code demonstrations as few-shot examples. Outperforms fine-tuned models by 11% on test assertion generation.
- Relation: Methodological precedent for retrieving project-specific examples; our work extends this with automated instruction optimization.

## Automated Prompt Optimization

**Zhou et al., "Large Language Models Are Human-Level Prompt Engineers" (APE), ICLR 2023.**
- Summary: Treats instructions as programs to be optimized by searching over LLM-generated candidates. Matched or surpassed human-written prompts on all 24 Instruction Induction tasks.
- Relation: Foundational prompt optimization paper establishing the feasibility of automated instruction generation.

**Khattab et al., "DSPy: Compiling Declarative Language Model Calls into State-of-the-Art Pipelines," ICLR 2024.**
- Summary: Programming model that abstracts LM pipelines as parameterized modules optimized by a compiler. Outperforms standard few-shot by 25%+ (GPT-3.5) and 65%+ (llama2-13b-chat).
- Relation: Candidate optimization framework for our experiments; optimizes full pipelines (demonstrations + instructions), not just instruction strings.

**Ji et al., "Automated Prompt Generation for Code Intelligence," ASE 2025.**
- Summary: First evaluation of automated prompt generation for code intelligence tasks, achieving ~28% improvement on translation, ~58% on summarization, and ~84% on API recommendation.
- Relation: Validates that automated prompt optimization works for SE tasks; code review was not tested, which is the gap our project fills.
