#ifndef FILTERS_H
#define FILTERS_H

/**
 * @brief Statistic block for min/max/avg
 */
class MinMaxAvgStatistic {
  float min_;
  float max_;
  float sum_;
  int count_;

public:
  /**
   * @brief Initialize the Statistic block
   */
  MinMaxAvgStatistic() : min_(NAN), max_(NAN), sum_(0), count_(0){}

  /**
   * @brief Add value to the statistic
   */
  void process(float value) {
    min_ = isnan(min_) ? value : min(min_, value);
    max_ = isnan(max_) ? value : max(max_, value);
    sum_ += value;
    count_++;
  }

  /**
   * @brief Resets the stored values
   */
  void reset() {
    min_ = NAN;
    max_ = NAN;
    sum_ = 0;
    count_ = 0;
  }

  /**
   * @brief Get Minimum
   * @return Minimum Value
   */
  float minimum() const {
    return min_;
  }

  /**
   * @brief Get Maximum
   * @return Maximum Value
   */
  float maximum() const {
    return max_;
  }

  /**
   * @brief Get Average
   * @return Average Value
   */
  float average() const {
    return sum_/count_;
  }
};

/**
 * @brief High Pass Filter
 */
class HighPassFilter {
  const float kX;
  const float kA0;
  const float kA1;
  const float kB1;
  float last_filter_value_;
  float last_raw_value_;

public:
  HighPassFilter(float samples) :
    kX(exp(-1/samples)), kA0((1+kX)/2), kA1(-kA0), kB1(kX),
    last_filter_value_(NAN), last_raw_value_(NAN){}

  HighPassFilter(float cutoff, float sampling_frequency) :
    HighPassFilter(sampling_frequency/(cutoff*2*PI)){}

  float process(float value) {
    if(isnan(last_filter_value_) || isnan(last_raw_value_)) {
      last_filter_value_ = 0.0;
    } else {
      last_filter_value_ = kA0 * value + kA1 * last_raw_value_ + kB1 * last_filter_value_;
    }
    last_raw_value_ = value;
    return last_filter_value_;
  }

  void reset() {
    last_raw_value_ = NAN;
    last_filter_value_ = NAN;
  }
};

/**
 * @brief Low Pass Filter
 */
class LowPassFilter {
  const float kX;
  const float kA0;
  const float kB1;
  float last_value_;

public:
  LowPassFilter(float samples) :
    kX(exp(-1/samples)), kA0(1-kX), kB1(kX), last_value_(NAN){}

  LowPassFilter(float cutoff, float sampling_frequency) :
    LowPassFilter(sampling_frequency/(cutoff*2*PI)){}

  float process(float value) {
    if(isnan(last_value_)) {
      last_value_ = value;
    } else {
      last_value_ = kA0 * value + kB1 * last_value_;
    }
    return last_value_;
  }

  void reset() {
    last_value_ = NAN;
  }
};

/**
 * @brief Differentiator
 */
class Differentiator {
  const float kSamplingFrequency;
  float last_value_;

public:
  Differentiator(float sampling_frequency) :
    kSamplingFrequency(sampling_frequency), last_value_(NAN){}

  float process(float value) {
    float diff = (value-last_value_)*kSamplingFrequency;
    last_value_ = value;
    return diff;
  }

  void reset() {
    last_value_ = NAN;
  }
};

/**
 * @brief MovingAverageFilter
 * @tparam buffer_size Number of samples to average over
 */
template<int kBufferSize>
class MovingAverageFilter {
  int index_;
  int count_;
  float values_[kBufferSize];

public:
  MovingAverageFilter() : index_(0), count_(0){}

  float process(float value) {
    values_[index_] = value;
    index_ = (index_ + 1) % kBufferSize;
    if(count_ < kBufferSize) {
      count_++;
    }

    float sum = 0.0;
    for(int i = 0; i < count_; i++) {
      sum += values_[i];
    }
    return sum/count_;
  }

  void reset() {
    index_ = 0;
    count_ = 0;
  }

  int count() const {
    return count_;
  }
};

#endif // FILTERS_H
