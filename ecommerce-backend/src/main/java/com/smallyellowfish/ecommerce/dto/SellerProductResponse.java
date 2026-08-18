package com.smallyellowfish.ecommerce.dto;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/** 卖家商品及售卖状态，供卖家在平台查看自己发布的二手商品。 */
public record SellerProductResponse(
    Long productId,
    String name,
    String category,
    BigDecimal price,
    Integer stockQuantity,
    String saleStatus,
    String approvalId,
    String approvalStatus,
    LocalDateTime soldAt,
    String buyerUserId,
    String soldOrderNo
) {
}
