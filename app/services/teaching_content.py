"""
Teaching Content Service - Load and manage teaching content from YAML files.
"""
import os
import yaml
from pathlib import Path
from typing import Optional
from app.server_logging import get_logger

logger = get_logger(__name__)

# Path to teaching content directory
TEACHING_CONTENT_DIR = Path(__file__).parent.parent / "lessons_data" / "teaching_content"


class TeachingContentService:
    """Service to load and query teaching content from YAML files."""

    def __init__(self):
        self._content_cache: dict[str, dict] = {}
        self._load_all_content()

    def _load_all_content(self) -> None:
        """Load all YAML files from teaching_content directory into cache."""
        if not TEACHING_CONTENT_DIR.exists():
            logger.warning(f"Teaching content directory not found: {TEACHING_CONTENT_DIR}")
            return

        for category_dir in TEACHING_CONTENT_DIR.iterdir():
            if not category_dir.is_dir():
                continue

            category = category_dir.name
            logger.info(f"Loading teaching content from category: {category}")

            for yaml_file in category_dir.glob("*.yaml"):
                try:
                    with open(yaml_file, "r", encoding="utf-8") as f:
                        content = yaml.safe_load(f)

                    if not content or not isinstance(content, dict):
                        logger.warning(f"Invalid YAML content in {yaml_file}")
                        continue

                    topic_id = content.get("topic_id")
                    if not topic_id:
                        logger.warning(f"Missing topic_id in {yaml_file}")
                        continue

                    # Add category and file path to content
                    content["_category"] = category
                    content["_file_path"] = str(yaml_file)

                    self._content_cache[topic_id] = content
                    logger.info(f"Loaded topic: {topic_id} from {yaml_file.name}")

                except Exception as e:
                    logger.error(f"Error loading {yaml_file}: {e}", exc_info=True)

        logger.info(f"Total teaching topics loaded: {len(self._content_cache)}")

    def get_topic(self, topic_id: str) -> Optional[dict]:
        """
        Get a teaching topic by its ID.

        Args:
            topic_id: The unique identifier of the topic

        Returns:
            Topic content dict or None if not found
        """
        return self._content_cache.get(topic_id)

    def get_all_topics(self, category: Optional[str] = None, level: Optional[str] = None) -> list[dict]:
        """
        Get all topics, optionally filtered by category and/or level.

        Args:
            category: Filter by category (e.g., "vocabulary", "grammar")
            level: Filter by level (e.g., "beginner", "intermediate")

        Returns:
            List of topic content dicts
        """
        topics = list(self._content_cache.values())

        if category:
            topics = [t for t in topics if t.get("_category") == category or t.get("category") == category]

        if level:
            topics = [t for t in topics if t.get("level") == level]

        return topics

    def get_topics_by_category(self, category: str) -> list[dict]:
        """Get all topics in a specific category."""
        return self.get_all_topics(category=category)

    def get_topic_ids(self) -> list[str]:
        """Get list of all available topic IDs."""
        return list(self._content_cache.keys())

    def reload(self) -> None:
        """Reload all content from files (useful for development/updates)."""
        self._content_cache.clear()
        self._load_all_content()

    def validate_topic(self, topic: dict) -> tuple[bool, list[str]]:
        """
        Validate if a topic has all required fields.

        Args:
            topic: Topic content dict

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        required_fields = ["topic_id", "title", "level", "category", "vocabulary", "teaching_strategies"]

        for field in required_fields:
            if field not in topic:
                errors.append(f"Missing required field: {field}")

        # Validate vocabulary items
        if "vocabulary" in topic:
            if not isinstance(topic["vocabulary"], list) or len(topic["vocabulary"]) == 0:
                errors.append("vocabulary must be a non-empty list")
            else:
                for idx, item in enumerate(topic["vocabulary"]):
                    if not isinstance(item, dict):
                        errors.append(f"vocabulary item {idx} must be a dict")
                        continue
                    if "word" not in item:
                        errors.append(f"vocabulary item {idx} missing 'word' field")
                    if "meaning_vi" not in item:
                        errors.append(f"vocabulary item {idx} missing 'meaning_vi' field")

        return (len(errors) == 0, errors)


# Singleton instance
_teaching_content_service: Optional[TeachingContentService] = None


def get_teaching_content_service() -> TeachingContentService:
    """Get or create the singleton TeachingContentService instance."""
    global _teaching_content_service
    if _teaching_content_service is None:
        _teaching_content_service = TeachingContentService()
    return _teaching_content_service
