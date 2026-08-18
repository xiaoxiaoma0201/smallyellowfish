package com.smallyellowfish.ecommerce.repository;

import com.smallyellowfish.ecommerce.entity.FaqEntry;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface FaqEntryRepository extends JpaRepository<FaqEntry, Long> {

    List<FaqEntry> findByCategoryContainingIgnoreCaseOrQuestionContainingIgnoreCaseOrAnswerContainingIgnoreCase(
        String categoryKeyword,
        String questionKeyword,
        String answerKeyword);
}
