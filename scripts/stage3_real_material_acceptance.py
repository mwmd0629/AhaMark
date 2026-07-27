"""Run bounded Stage 3 acceptance cases derived from the read-only 2025 A1 solution.

The source is used only as provenance. All submitted answers below are explicitly
synthetic stage3_e2e variants; no source PDF is modified or bulk-imported.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from app.math_validation.engine import validate

SOURCE = r"D:\梅往眉颠\大一下\线性代数A1\final\2025LAA1finalsol.pdf"
LIMITS = {
    "timeout_ms": 500,
    "max_expression_length": 4096,
    "max_nodes": 1000,
    "max_matrix_size": 12,
    "max_polynomial_degree": 20,
    "max_variables": 8,
    "max_expansion_terms": 500,
}


def rule(answer_type: str, **extra: Any) -> dict[str, Any]:
    return {
        "answer_type": answer_type,
        "domain": "rational",
        "limits": LIMITS,
        **extra,
    }


def cases() -> list[dict[str, Any]]:
    homogeneous = {"matrix": [[1, 1, 1], [2, 2, 2]], "target": [0, 0]}
    image_space = [[1, -1, 0, 0]]
    kernel_space = [[1, 0, 0, 1], [0, 1, 1, 0], [0, 1, 0, 1]]
    block_matrix = [[2, 1], [1, 2]]
    eigen_matrix = [[2, 0, 0], [0, 2, 0], [0, 0, 3]]
    result: list[dict[str, Any]] = []

    def add(
        category: str,
        variant: str,
        answer_type: str,
        candidate: Any,
        reference: Any,
        expected: str,
        **extra: Any,
    ) -> None:
        result.append(
            {
                "category": category,
                "variant": variant,
                "rule": rule(answer_type, **extra),
                "candidate": candidate,
                "reference": reference,
                "expected": expected,
            }
        )

    add(
        "homogeneous_linear_system",
        "correct_different_expression",
        "linear_system_candidate",
        [2, -3, 1],
        homogeneous,
        "verified_pass",
    )
    add(
        "homogeneous_linear_system",
        "equivalent_different_basis",
        "linear_system_basis",
        [[0, 2, -2], [-3, 0, 3]],
        {"matrix": homogeneous["matrix"]},
        "verified_pass",
    )
    add(
        "homogeneous_linear_system",
        "wrong_final_intermediate_correct",
        "linear_system_candidate",
        [1, 0, 0],
        homogeneous,
        "verified_fail",
    )
    add(
        "homogeneous_linear_system",
        "correct_final_proof_missing",
        "linear_system_classification",
        "infinite",
        homogeneous,
        "verified_pass",
    )
    add(
        "homogeneous_linear_system",
        "explicitly_wrong",
        "linear_system_basis",
        [[1, -1, 0], [2, -2, 0]],
        {"matrix": homogeneous["matrix"]},
        "verified_fail",
    )
    add(
        "homogeneous_linear_system",
        "proof_required",
        "proof_step",
        {"claim": "these vectors form a fundamental system"},
        {},
        "manual_required",
    )

    add(
        "image_and_kernel",
        "correct_different_expression",
        "subspace_membership",
        [3, -3, 0, 0],
        image_space,
        "verified_pass",
    )
    add(
        "image_and_kernel",
        "equivalent_different_basis",
        "subspace_basis",
        [[0, 3, 3, 0], [2, 0, 0, 2], [0, -1, 0, -1]],
        kernel_space,
        "verified_pass",
    )
    add(
        "image_and_kernel",
        "wrong_final_intermediate_correct",
        "subspace_dimension",
        2,
        kernel_space,
        "verified_fail",
    )
    add(
        "image_and_kernel",
        "correct_final_proof_missing",
        "subspace_dimension",
        3,
        kernel_space,
        "verified_pass",
    )
    add(
        "image_and_kernel",
        "explicitly_wrong",
        "subspace_basis",
        [[1, 0, 0, 1], [2, 0, 0, 2]],
        kernel_space,
        "verified_fail",
    )
    add(
        "image_and_kernel",
        "proof_required",
        "proof_step",
        {"claim": "image/kernel derivation"},
        {},
        "manual_required",
    )

    add(
        "matrix_or_block_relation",
        "correct_different_expression",
        "matrix",
        [["2/1", "1"], ["1", "2"]],
        block_matrix,
        "verified_pass",
    )
    add(
        "matrix_or_block_relation",
        "equivalent_different_basis",
        "diagonalization",
        {"P": [[1, 1], [1, -1]], "D": [[3, 0], [0, 1]]},
        block_matrix,
        "verified_pass",
    )
    add(
        "matrix_or_block_relation",
        "wrong_final_intermediate_correct",
        "diagonalization",
        {"P": [[1, 1], [1, -1]], "D": [[3, 0], [0, 2]]},
        block_matrix,
        "verified_fail",
    )
    add(
        "matrix_or_block_relation",
        "correct_final_proof_missing",
        "matrix",
        block_matrix,
        block_matrix,
        "verified_pass",
    )
    add(
        "matrix_or_block_relation",
        "explicitly_wrong",
        "matrix",
        [[2, 0], [1, 2]],
        block_matrix,
        "verified_fail",
    )
    add(
        "matrix_or_block_relation",
        "induction_required",
        "proof_step",
        {"claim": "block pattern holds for every k"},
        {},
        "manual_required",
    )

    add(
        "eigen_or_minimal_polynomial",
        "correct_different_expression",
        "eigenvalue_multiset",
        [3, 2, 2],
        eigen_matrix,
        "verified_pass",
    )
    add(
        "eigen_or_minimal_polynomial",
        "equivalent_different_basis",
        "eigenspace_basis",
        [[0, 5, 0], [-2, 0, 0]],
        {"matrix": eigen_matrix, "eigenvalue": 2},
        "verified_pass",
    )
    add(
        "eigen_or_minimal_polynomial",
        "wrong_final_intermediate_correct",
        "eigenvalue_multiset",
        [2, 3],
        eigen_matrix,
        "verified_fail",
    )
    add(
        "eigen_or_minimal_polynomial",
        "correct_final_proof_missing",
        "minimal_polynomial",
        {"coefficients": [-2, 1]},
        {"matrix": [[2, 0], [0, 2]]},
        "verified_pass",
        prove_minimal=True,
        variable="x",
    )
    add(
        "eigen_or_minimal_polynomial",
        "explicitly_wrong",
        "eigenvector",
        [0, 0, 1],
        {"matrix": eigen_matrix, "eigenvalue": 2},
        "verified_fail",
    )
    add(
        "eigen_or_minimal_polynomial",
        "annihilates_but_minimality_unproved",
        "minimal_polynomial",
        {"coefficients": [4, -4, 1]},
        {"matrix": [[2, 0], [0, 2]]},
        "indeterminate",
        prove_minimal=False,
        variable="x",
    )

    for variant in (
        "correct_different_expression",
        "equivalent_different_basis",
        "wrong_final_intermediate_correct",
        "correct_final_proof_missing",
        "explicitly_wrong",
        "indeterminate_or_manual",
    ):
        add(
            "proof_item",
            variant,
            "proof_step",
            {"variant": variant, "claim": "proof text requires teacher judgment"},
            {},
            "manual_required",
        )
    return result


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    records = []
    for index, case in enumerate(cases(), start=1):
        outcome = validate(case["rule"], case["candidate"], case["reference"])
        records.append(
            {
                "id": f"stage3_e2e_{index:02d}",
                "synthetic": True,
                "source": {
                    "path": SOURCE,
                    "source_type": "imported_reference",
                    "page": 1 if index <= 12 else 2,
                    "note": "2025 solution with step points; provenance only",
                },
                "category": case["category"],
                "variant": case["variant"],
                "expected": case["expected"],
                "actual": outcome.result,
                "reason": outcome.reason,
                "matched": outcome.result == case["expected"],
            }
        )
    report = {
        "marker": "stage3_e2e",
        "source_read_only": True,
        "source_modified": False,
        "sample_count": len(records),
        "matched_count": sum(item["matched"] for item in records),
        "all_matched": all(item["matched"] for item in records),
        "records": records,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    if not report["all_matched"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
