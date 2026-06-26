"""
Adaptive Teaching Engine - Generate dynamic lesson plans and manage teaching flow.
"""
import json
from typing import Optional
from app.server_logging import get_logger
from app.services.llm import LLMService

logger = get_logger(__name__)


class TeachingStep:
    """Represents a single step in the teaching process."""
    
    INTRO = "intro"
    PRESENT_WORD = "present_word"
    ASK_REPEAT = "ask_repeat"
    ASSESS = "assess"
    SUMMARY = "summary"
    PRAISE = "praise"
    CORRECT = "correct"

    def __init__(self, step_type: str, data: dict):
        self.step_type = step_type
        self.data = data

    def to_dict(self) -> dict:
        return {
            "step_type": self.step_type,
            "data": self.data
        }


class AdaptiveTeachingEngine:
    """Engine to generate and manage adaptive teaching flows."""

    def __init__(self, llm_service: LLMService):
        self._llm = llm_service

    def generate_lesson_plan(self, topic: dict) -> list[TeachingStep]:
        """
        Generate a structured lesson plan from topic content.
        
        Args:
            topic: Topic content dict from YAML
            
        Returns:
            List of TeachingStep objects
        """
        steps = []
        
        # Step 1: Introduction
        steps.append(TeachingStep(
            TeachingStep.INTRO,
            {
                "title": topic.get("title", ""),
                "learning_objectives": topic.get("learning_objectives", []),
                "duration_minutes": topic.get("duration_minutes", 10)
            }
        ))
        
        # Steps 2-N: Present each vocabulary word
        vocabulary = topic.get("vocabulary", [])
        for idx, vocab_item in enumerate(vocabulary):
            # Present word
            steps.append(TeachingStep(
                TeachingStep.PRESENT_WORD,
                {
                    "word": vocab_item.get("word", ""),
                    "pronunciation": vocab_item.get("pronunciation", ""),
                    "meaning_vi": vocab_item.get("meaning_vi", ""),
                    "examples": vocab_item.get("examples", []),
                    "related_objects": vocab_item.get("related_objects", []),
                    "index": idx,
                    "total": len(vocabulary)
                }
            ))
            
            # Ask student to repeat
            steps.append(TeachingStep(
                TeachingStep.ASK_REPEAT,
                {
                    "word": vocab_item.get("word", ""),
                    "meaning_vi": vocab_item.get("meaning_vi", ""),
                }
            ))
        
        # Step N+1: Assessment
        assessment_questions = topic.get("assessment_questions", [])
        if assessment_questions:
            steps.append(TeachingStep(
                TeachingStep.ASSESS,
                {
                    "questions": assessment_questions
                }
            ))
        
        # Step N+2: Summary
        steps.append(TeachingStep(
            TeachingStep.SUMMARY,
            {
                "topic_title": topic.get("title", ""),
                "vocabulary_count": len(vocabulary)
            }
        ))
        
        return steps

    async def generate_teaching_prompt(
        self,
        step: TeachingStep,
        topic: dict,
        student_response: str = "",
        student_history: Optional[list[dict]] = None
    ) -> str:
        """
        Generate a detailed prompt for LLM to respond as a teacher.
        
        Args:
            step: Current teaching step
            topic: Full topic content
            student_response: What the student just said
            student_history: Previous interactions (for context)
            
        Returns:
            Formatted prompt string for LLM
        """
        teaching_strategies = topic.get("teaching_strategies", [])
        strategies_text = "\n".join(f"- {s}" for s in teaching_strategies)
        
        if step.step_type == TeachingStep.INTRO:
            return f"""You are a friendly, enthusiastic English teacher for young children (age {topic.get('target_age', '3-6')}).

**Current Task:** Introduce the lesson about "{step.data['title']}"

**Learning Objectives:**
{chr(10).join(f"- {obj}" for obj in step.data['learning_objectives'])}

**Teaching Strategies:**
{strategies_text}

**Instructions:**
- Greet the student warmly
- Introduce the topic in an exciting way
- Explain what we will learn today
- Use simple, clear language
- Be encouraging and positive

**Student said:** "{student_response if student_response else '[Starting lesson]'}"

**Respond as the teacher (in Vietnamese or English as appropriate):**"""

        elif step.step_type == TeachingStep.PRESENT_WORD:
            word_data = step.data
            examples_text = "\n".join(
                f"  - \"{ex['text']}\" ({ex.get('translation', '')})"
                for ex in word_data.get('examples', [])
            )
            
            return f"""You are teaching the word: **{word_data['word']}**

**Word Details:**
- Pronunciation: {word_data.get('pronunciation', '')}
- Meaning (Vietnamese): {word_data.get('meaning_vi', '')}
- Examples:
{examples_text}
- Related objects: {', '.join(word_data.get('related_objects', []))}

**Progress:** Word {word_data.get('index', 0) + 1} of {word_data.get('total', 0)}

**Teaching Strategies:**
{strategies_text}

**Instructions:**
- Introduce the word clearly
- Say the pronunciation
- Give the Vietnamese meaning
- Provide at least one example
- Make it fun and engaging
- Use objects/images references if helpful

**Student said:** "{student_response if student_response else '[Ready to learn]'}"

**Respond as the teacher:**"""

        elif step.step_type == TeachingStep.ASK_REPEAT:
            return f"""You just taught the word: **{step.data['word']}** (meaning: {step.data['meaning_vi']})

**Current Task:** Ask the student to repeat the word

**Instructions:**
- Encouragingly ask the student to say the word
- Be gentle and supportive
- You can say something like: "Now, can you say '{step.data['word']}'?" or "Let's practice together: '{step.data['word']}'"
- Keep it simple and clear

**Student said:** "{student_response if student_response else '[Waiting]'}"

**Respond as the teacher:**"""

        elif step.step_type == TeachingStep.ASSESS:
            questions_text = "\n".join(
                f"- {q['question']}" 
                for q in step.data.get('questions', [])
            )
            
            return f"""**Assessment Time!**

**Available Questions:**
{questions_text}

**Instructions:**
- Ask ONE assessment question to check understanding
- Be encouraging
- If the student answers correctly, praise them warmly
- If incorrect, gently correct and explain

**Student said:** "{student_response if student_response else '[Ready for quiz]'}"

**Respond as the teacher:**"""

        elif step.step_type == TeachingStep.SUMMARY:
            return f"""**Lesson Wrap-Up**

**Topic:** {step.data.get('topic_title', '')}
**Words Learned:** {step.data.get('vocabulary_count', 0)}

**Instructions:**
- Summarize what was learned today
- Praise the student's effort and progress
- Encourage continued practice
- End with an enthusiastic, positive note

**Student said:** "{student_response if student_response else '[Lesson complete]'}"

**Respond as the teacher:**"""

        elif step.step_type == TeachingStep.PRAISE:
            return f"""**Great job!**

The student said: "{student_response}"

**Instructions:**
- Give warm, specific praise
- Acknowledge what they did well
- Encourage them to continue
- Be genuinely enthusiastic

**Respond as the teacher:**"""

        elif step.step_type == TeachingStep.CORRECT:
            return f"""**Gentle Correction Needed**

The student said: "{student_response}"
Expected: {step.data.get('expected', '')}

**Instructions:**
- Gently explain what was incorrect
- Provide the correct answer
- Give another example
- Encourage them to try again
- Never be harsh or critical
- End on a positive note

**Respond as the teacher:**"""

        return "Continue the lesson naturally as a supportive teacher."

    async def evaluate_student_response(
        self,
        student_text: str,
        expected_answer: Optional[str] = None,
        step_type: str = TeachingStep.ASK_REPEAT
    ) -> dict:
        """
        Evaluate if student's response is correct/appropriate.
        
        Args:
            student_text: What the student said
            expected_answer: What was expected (if applicable)
            step_type: Type of current teaching step
            
        Returns:
            Dict with evaluation results: {
                "is_correct": bool,
                "confidence": float,
                "feedback": str
            }
        """
        if not student_text or not student_text.strip():
            return {
                "is_correct": False,
                "confidence": 0.0,
                "feedback": "No response detected"
            }

        # For repeat tasks, check if student said the word
        if step_type == TeachingStep.ASK_REPEAT and expected_answer:
            student_lower = student_text.lower().strip()
            expected_lower = expected_answer.lower().strip()
            
            # Simple similarity check
            if expected_lower in student_lower or student_lower in expected_lower:
                return {
                    "is_correct": True,
                    "confidence": 0.9,
                    "feedback": "Great pronunciation!"
                }
            
            # Use LLM for more nuanced evaluation
            try:
                prompt = f"""Evaluate if the student's pronunciation attempt is acceptable.

Expected word: "{expected_answer}"
Student said: "{student_text}"

Is this acceptable? Consider:
- Similar pronunciation (phonetic similarity)
- Vietnamese accent variations
- Partial attempts

Respond with JSON:
{{"is_correct": true/false, "confidence": 0.0-1.0, "feedback": "brief comment"}}"""

                result = await self._llm.chat_json(prompt, system_prompt="You are an evaluation assistant.")
                if result and isinstance(result, dict):
                    return result
            except Exception as e:
                logger.error(f"LLM evaluation failed: {e}")

        # Default: student didn't match — don't auto-pass
        return {
            "is_correct": False,
            "confidence": 0.0,
            "feedback": "Hmm, chưa đúng lắm. Hãy thử lại nhé!"
        }

    def get_next_step_index(
        self,
        current_index: int,
        total_steps: int,
        skip_forward: bool = False
    ) -> int:
        """Calculate next step index based on current progress."""
        if skip_forward:
            return min(current_index + 2, total_steps - 1)
        return min(current_index + 1, total_steps - 1)

    def is_lesson_complete(self, current_index: int, total_steps: int) -> bool:
        """Check if the lesson is complete."""
        return current_index >= total_steps - 1
