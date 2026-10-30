def can_rent(customer, car):
    if customer.age > 25:
        return True
    if customer.age > 21 and car.category == "economy":
        return True
    return False


def late_fee(days_late, daily_rate):
    if days_late <= 0:
        return 0
    return days_late * daily_rate
