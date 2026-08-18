package com.smallyellowfish.ecommerce.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.servers.Server;
import java.util.Collections;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class ApiDocumentationConfig {

    @Bean
    public OpenAPI ecommerceOpenApi() {
        return new OpenAPI()
            .info(new Info()
                .title("小黄鱼二手电商交易平台客服/售后业务 API")
                .version("v1")
                .description("小黄鱼二手电商交易平台客服/售后 Agent 业务后端接口文档"))
            .servers(Collections.singletonList(new Server().url("/").description("Current deployment base URL")));
    }
}
