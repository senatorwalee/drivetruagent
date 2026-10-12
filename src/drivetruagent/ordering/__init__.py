"""The rules of ordering: sessions, carts, pricing and order submission.

Nothing in this package knows about HTTP or about the model. Whatever the
agent proposes ends up as a call to one of these functions, which either does
the work or raises an OrderingError explaining why not.
"""
