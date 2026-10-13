"""Evals: whole conversations, judged on what the backend ended up holding.

A scenario is what a customer says, turn by turn, plus what must be true
afterwards. The checks look at the cart, the order, the session's state and
which tools were used. They do not judge the agent's wording, so the same
conversation always gets the same verdict.
"""
