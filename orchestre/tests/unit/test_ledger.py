from decimal import Decimal as D

import pytest

from orchestre.portfolio.ledger import Ledger, LedgerError


def test_allocate_moves_from_unallocated():
    lg = Ledger(D("500"))
    lg.allocate("dca", D("100"))
    assert lg.unallocated == D("400") and lg.account("dca").allocated == D("100")


def test_cannot_over_allocate():
    lg = Ledger(D("100"))
    with pytest.raises(LedgerError):
        lg.allocate("dca", D("101"))


def test_commit_and_release():
    lg = Ledger(D("500"))
    lg.allocate("dca", D("100"))
    lg.commit("dca", D("30"))
    assert lg.account("dca").available == D("70")
    lg.release("dca", D("10"))
    assert lg.account("dca").available == D("80")


def test_cannot_commit_beyond_allocation():
    lg = Ledger(D("500"))
    lg.allocate("dca", D("100"))
    with pytest.raises(LedgerError):
        lg.commit("dca", D("100.01"))


def test_cannot_release_more_than_committed():
    lg = Ledger(D("500"))
    lg.allocate("dca", D("100"))
    lg.commit("dca", D("10"))
    with pytest.raises(LedgerError):
        lg.release("dca", D("11"))


def test_reduce_allocation_only_down_to_committed():
    lg = Ledger(D("500"))
    lg.allocate("dca", D("100"))
    lg.commit("dca", D("40"))
    lg.deallocate("dca", D("60"))
    assert lg.unallocated == D("460")
    with pytest.raises(LedgerError):
        lg.deallocate("dca", D("1"))


def test_total_is_conserved_across_operations():
    lg = Ledger(D("500"))
    lg.allocate("a", D("120"))
    lg.allocate("b", D("80"))
    lg.commit("a", D("50"))
    lg.deallocate("b", D("30"))
    assert lg.unallocated + sum(a.allocated for a in lg.accounts()) == D("500")


def test_total_capital_is_capped():
    lg = Ledger(D("500"))
    assert lg.total == D("500")
    with pytest.raises(LedgerError):
        Ledger(D("-1"))


def test_negative_amounts_refused():
    lg = Ledger(D("500"))
    with pytest.raises(LedgerError):
        lg.allocate("a", D("-5"))
