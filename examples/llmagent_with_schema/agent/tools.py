# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the agent. """

from typing import List


def get_user_interests_analysis(interests: List[str]) -> dict:
    """Analyze user interests and return relevant suggestions"""
    # Simulate interest analysis API call
    interest_analysis = {
        "programming": {
            "personality": "Logical thinking",
            "activities": ["Programming marathon", "Open source project", "Technical conference"]
        },
        "fitness": {
            "personality": "Self-discipline",
            "activities": ["Gym", "Outdoor activity", "Marathon"]
        },
    }

    analysis_result = {"personality_traits": [], "recommended_activities": []}

    for interest in interests:
        if interest in interest_analysis:
            analysis_result["personality_traits"].append(interest_analysis[interest]["personality"])
            analysis_result["recommended_activities"].extend(interest_analysis[interest]["activities"])

    return analysis_result


def calculate_profile_score(age: int, interests: List[str], location: str) -> int:
    """Calculate the completeness score of user profiles"""
    score = 5  # Base score

    # Age Score
    if 18 <= age <= 60:
        score += 2
    else:
        score += 1

    # Interest Quantity Score
    if len(interests) >= 3:
        score += 2
    elif len(interests) >= 1:
        score += 1

    # Location Information Score
    if location:
        score += 1

    return min(score, 10)  # Max 10 points
