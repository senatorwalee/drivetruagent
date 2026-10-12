"""The layer between "the model proposed an action" and "the action happened".

The model proposes; code decides. Every proposal is allowed, rejected, held
for the customer's confirmation, or escalated to a person, by rules written
in Python. None of them live in a prompt, so a model that ignores its
instructions still cannot get past them.
"""
