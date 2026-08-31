import re
from typing import Dict, List, Optional, Sequence, Union

from egpt.exceptions import CheckTrigger, InputCheckError
from egpt.guardrails.base import BaseGuardrail
from egpt.run.agent import RunInput
from egpt.run.team import TeamRunInput


class PIIDetectionGuardrail(BaseGuardrail):
    """Guardrail for detecting Personally Identifiable Information (PII).

    Args:
        mask_pii: Whether to mask the PII in the input, rather than raising an error.
        enable_ssn_check: Whether to check for Social Security Numbers. True by default.
        enable_credit_card_check: Whether to check for credit cards. True by default.
        enable_email_check: Whether to check for emails. True by default.
        enable_phone_check: Whether to check for phone numbers. True by default.
        custom_patterns: A dictionary of custom PII patterns to detect, mapping a name to a regex string or
            compiled pattern. This is added to the default patterns.
    """

    def __init__(
        self,
        mask_pii: bool = False,
        enable_ssn_check: bool = True,
        enable_credit_card_check: bool = True,
        enable_email_check: bool = True,
        enable_phone_check: bool = True,
        custom_patterns: Optional[Dict[str, Union[str, re.Pattern[str]]]] = None,
    ):
        self.mask_pii = mask_pii
        self.pii_patterns = {}

        if enable_ssn_check:
            self.pii_patterns["SSN"] = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
        if enable_credit_card_check:
            self.pii_patterns["Credit Card"] = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
        if enable_email_check:
            self.pii_patterns["Email"] = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
        if enable_phone_check:
            self.pii_patterns["Phone"] = re.compile(r"\b\d{3}[\s.-]?\d{3}[\s.-]?\d{4}\b")

        if custom_patterns:
            # Compile string patterns up front so an invalid regex fails here, not on the first check()
            for name, pattern in custom_patterns.items():
                if isinstance(pattern, str):
                    self.pii_patterns[name] = re.compile(pattern)
                elif isinstance(pattern, re.Pattern):
                    self.pii_patterns[name] = pattern
                else:
                    raise TypeError(
                        f"custom_patterns['{name}'] must be a str or re.Pattern, got {type(pattern).__name__}"
                    )

    def detect(self, content: str) -> List[str]:
        """Which of this guardrail's PII types are present in ``content``.

        The read-only half of the check, shared by :meth:`check`/:meth:`async_check` (which
        raise or mask based on it) and any other caller with a plain string and no
        ``RunInput``/``TeamRunInput`` to check against -- e.g. the memory write-back/read-inject
        boundaries in ``egpt.memory.security.pii``.
        """
        return [pii_type for pii_type, pattern in self.pii_patterns.items() if pattern.search(content)]

    def mask(self, content: str, pii_types: Optional[Sequence[str]] = None) -> str:
        """Mask every match of ``pii_types`` in ``content`` with ``*`` of the same length.

        ``pii_types`` defaults to whatever :meth:`detect` finds in ``content`` -- pass the result
        of a prior ``detect()`` call to avoid scanning the content twice.
        """
        types = pii_types if pii_types is not None else self.detect(content)
        for pii_type in types:
            pattern = self.pii_patterns[pii_type]

            def mask_match(match: "re.Match[str]") -> str:
                return "*" * len(match.group(0))

            content = pattern.sub(mask_match, content)
        return content

    def check(self, run_input: Union[RunInput, TeamRunInput]) -> None:
        """Check for PII patterns in the input."""
        content = run_input.input_content_string()
        detected_pii = self.detect(content)
        if detected_pii:
            if self.mask_pii:
                run_input.input_content = self.mask(content, detected_pii)
                return
            else:
                raise InputCheckError(
                    "Potential PII detected in input",
                    additional_data={"detected_pii": detected_pii},
                    check_trigger=CheckTrigger.PII_DETECTED,
                )

    async def async_check(self, run_input: Union[RunInput, TeamRunInput]) -> None:
        """Asynchronously check for PII patterns in the input."""
        content = run_input.input_content_string()
        detected_pii = self.detect(content)
        if detected_pii:
            if self.mask_pii:
                run_input.input_content = self.mask(content, detected_pii)
                return
            else:
                raise InputCheckError(
                    "Potential PII detected in input",
                    additional_data={"detected_pii": detected_pii},
                    check_trigger=CheckTrigger.PII_DETECTED,
                )
