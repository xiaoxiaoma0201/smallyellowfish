package com.smallyellowfish.ecommerce.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.smallyellowfish.ecommerce.dto.AgentChatRequest;
import com.smallyellowfish.ecommerce.dto.AgentResumeRequest;

public interface CustomerServiceAgentClient {

    JsonNode chat(AgentChatRequest request);

    JsonNode resume(AgentResumeRequest request);
}
