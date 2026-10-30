def can_rent(customer, car):
    if customer.banned:
        return False
    if customer.age >= 25:
        return True
    if customer.age >= 21 and car.category in ("economy", "compact"):
        return True
    return False


def late_fee(days_late, daily_rate, grace_days=1):
    if days_late <= grace_days:
        return 0
    return (days_late - grace_days) * daily_rate
