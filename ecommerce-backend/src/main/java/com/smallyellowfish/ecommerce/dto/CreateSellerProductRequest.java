package com.smallyellowfish.ecommerce.dto;

import java.math.BigDecimal;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

/** 卖家发布二手闲置商品的请求参数。 */
public record CreateSellerProductRequest(
    @NotBlank(message = "商品名称不能为空")
    String name,
    String category,
    String description,
    @NotNull(message = "价格不能为空")
    @DecimalMin(value = "0.01", message = "价格必须大于 0")
    BigDecimal price,
    @NotNull(message = "库存不能为空")
    @Min(value = 1, message = "库存至少为 1")
    Integer stock,
    String imageUrl
) {
}
