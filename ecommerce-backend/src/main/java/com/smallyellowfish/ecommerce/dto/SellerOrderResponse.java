package com.smallyellowfish.ecommerce.dto;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/** 卖家视角的卖出订单：买家购买了自己商品的订单及其履约状态。 */
public record SellerOrderResponse(
    String orderNo,
    String buyerUserId,
    String buyerName,
    String orderStatus,
    String paymentStatus,
    BigDecimal totalAmount,
    String itemSummary,
    String logisticsNo,
    Boolean canShip,
    LocalDateTime createdAt,
    LocalDateTime paidAt,
    LocalDateTime shippedAt,
    LocalDateTime deliveredAt
) {
}
